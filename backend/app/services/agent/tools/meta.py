"""工具注册层 · meta：创作角色 / 规则 / 生成插件 / MCP 服务器 配置加载与 CRUD。

从 agent_service.py 剥离（原行号 1027~1130 常量 + 5351~5760 CRUD），逻辑未改动。
包含 context 层依赖的两个加载器：_load_role_configs / _load_enabled_rules。
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ─── 常量：内置角色 / 内置插件（原 agent_service.py 1027~1129）────────────

# 创作角色预设（C：多角色 Agent）：人设 prompt + 工具白名单（None=全部工具）
_AGENT_ROLES: dict = {
    "screenwriter": {
        "name": "编剧",
        "prompt": (
            "你是一位资深短剧编剧，擅长故事结构（三幕/钩子/反转）、人物弧光、"
            "对白打磨与节奏把控。回答聚焦创意与文本，优先给出可落地的剧本结构、"
            "大纲、分集情节与对白。"
        ),
        "tools": ["create_project", "web_search", "github_search", "generate_image"],
    },
    "director": {
        "name": "导演",
        "prompt": (
            "你是一位影视导演，擅长镜头语言、景别调度、分镜设计、光影氛围与"
            "场面调度。回答聚焦视觉化呈现，用镜头/画面语言描述场景，并指导生成"
            "符合导演意图的视觉素材。"
        ),
        "tools": ["generate_image", "generate_video", "web_search", "github_search"],
    },
    "artist": {
        "name": "美术指导",
        "prompt": (
            "你是一位影视美术指导，擅长视觉风格统一、角色造型设计、场景氛围、"
            "色彩光影与材质细节。回答聚焦视觉美学，输出可直接用于生图的高质量"
            "画面描述与风格建议。"
            "2026-08-12 人种判定：角色人种需依据故事背景与角色名称/描述中的文化线索判断"
            "（中国/东亚题材按东亚人长相——黑/深色直发、黄/暖白肤色、深色眼睛、亚洲人种面部特征；"
            "西方/其他文化背景按对应人种设计），人种必须与故事设定一致，严禁与背景矛盾，"
            "严禁默认欧美白人金发碧眼或强行套用东亚长相。"
        ),
        "tools": ["generate_image", "generate_video"],
    },
}

# 生成插件内置定义（对齐 TraeWork 插件：对话前选择插件 → 描述需求 → 执行，2026-08-11）。
# 每个插件绑定一个内置工具 + 执行模式；prompt 为选中后注入 system prompt 的执行约束。
_BUILTIN_PLUGINS: list[dict] = [
    {
        "name": "t2i",
        "label": "文生图",
        "description": "用文字描述生成一张图片。",
        "prompt": (
            "已启用「文生图」插件。当用户描述画面需求时，调用 generate_image 工具生成图片，"
            "不要使用任何参考图（纯文字描述）。把用户需求整理成详细的中文画面描述（主体/动作/环境/光线/风格）。"
        ),
        "tool": "generate_image",
        "mode": "t2i",
        "sort": 1,
    },
    {
        "name": "i2i",
        "label": "图生图",
        "description": "基于用户上传的参考图生成相似风格/构图的新图。",
        "prompt": (
            "已启用「图生图」插件。用户会提供参考图（对话消息中已附带图片）。"
            "当用户描述需求时，调用 generate_image 工具并带上参考图（系统会自动把消息中的图片作为参考），"
            "生成与参考图风格/构图一致的图片。"
        ),
        "tool": "generate_image",
        "mode": "i2i",
        "sort": 2,
    },
    {
        "name": "t2v",
        "label": "文生视频",
        "description": "用文字描述生成一段视频（纯文生视频）。",
        "prompt": (
            "已启用「文生视频」插件。当用户描述动态画面需求时，调用 generate_video 工具生成视频，"
            "不要使用任何参考图。把需求整理成详细的中文画面描述（主体/动作/镜头运动/环境/氛围）。"
        ),
        "tool": "generate_video",
        "mode": "t2v",
        "sort": 3,
    },
    {
        "name": "i2v",
        "label": "图生视频",
        "description": "以用户上传的图片为首帧/参考生成视频。",
        "prompt": (
            "已启用「图生视频」插件。用户会提供一张参考图（对话消息中已附带图片，作为首帧/画面参考）。"
            "调用 generate_video 工具并带上该参考图，生成以它为画面基础的视频。"
        ),
        "tool": "generate_video",
        "mode": "i2v",
        "sort": 4,
    },
    {
        "name": "multi_ref",
        "label": "多图参考生视频",
        "description": "以多张参考图（角色/场景/风格）生成视频。",
        "prompt": (
            "已启用「多图参考生视频」插件。用户会提供多张参考图（角色外观、场景、风格等，消息中已附带图片）。"
            "调用 generate_video 工具并带上全部参考图，生成综合这些参考要素的视频。"
        ),
        "tool": "generate_video",
        "mode": "multi_ref",
        "sort": 5,
    },
]

_PLUGIN_MODES = ("t2i", "i2i", "t2v", "i2v", "multi_ref")


def _ensure_builtin_plugins(db: Session) -> None:
    """确保内置 5 种生成插件存在于 DB（缺失则创建，配置以代码为准同步）。"""
    from app.models.agent import AgentPlugin

    for p in _BUILTIN_PLUGINS:
        row = db.scalar(select(AgentPlugin).where(AgentPlugin.name == p["name"]))
        if row is None:
            db.add(AgentPlugin(
                name=p["name"], label=p["label"], description=p["description"],
                prompt=p["prompt"], tool=p["tool"], mode=p["mode"],
                is_builtin=True, enabled=True, sort=p["sort"],
            ))
        else:
            # 内置插件内容变更需同步更新，保障使用没问题（用户要求）
            row.label = p["label"]
            row.description = p["description"]
            row.prompt = p["prompt"]
            row.tool = p["tool"]
            row.mode = p["mode"]
            row.sort = p["sort"]
    db.commit()


# ─── 规则（Rules，对齐 TraeWork）──────────────────────

def _load_enabled_rules(db: Session, project_id=None) -> list[str]:
    """加载注入对话的规则文本：全局规则 + 当前项目的项目级规则（按 sort 排序）。"""
    from app.models.agent import AgentRule

    conditions = [AgentRule.enabled.is_(True)]
    if project_id:
        conditions.append(
            (AgentRule.scope == "global") | (
                (AgentRule.scope == "project") & (AgentRule.project_id == project_id)
            )
        )
    else:
        conditions.append(AgentRule.scope == "global")
    rules = list(
        db.scalars(
            select(AgentRule).where(*conditions).order_by(AgentRule.sort.asc(), AgentRule.created_at.asc())
        ).all()
    )
    return [f"- [{r.name}] {r.content}" for r in rules]


def list_rules(db: Session, project_id=None) -> list:
    from app.models.agent import AgentRule

    conditions = [AgentRule.scope == "global"]
    if project_id:
        conditions.append(AgentRule.project_id == project_id)
    return list(
        db.scalars(
            select(AgentRule).where(*conditions).order_by(AgentRule.sort.asc(), AgentRule.created_at.asc())
        ).all()
    )


def create_rule(db: Session, payload) -> object:
    from app.models.agent import AgentRule

    name = (payload.name or "").strip()
    content = (payload.content or "").strip()
    if not name or not content:
        raise ValueError("规则名称与内容不能为空")
    if payload.scope not in ("global", "project"):
        raise ValueError("scope 仅支持 global 或 project")
    if payload.scope == "project" and not payload.project_id:
        raise ValueError("项目级规则必须指定 project_id")
    rule = AgentRule(
        name=name,
        content=content,
        scope=payload.scope,
        project_id=payload.project_id if payload.scope == "project" else None,
        enabled=payload.enabled,
        sort=payload.sort,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def update_rule(db: Session, rule_id, payload) -> object:
    from app.models.agent import AgentRule

    rule = db.get(AgentRule, rule_id)
    if not rule:
        raise ValueError("规则不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("scope") not in (None, "global", "project"):
        raise ValueError("scope 仅支持 global 或 project")
    if data.get("scope") == "project" and not data.get("project_id"):
        raise ValueError("项目级规则必须指定 project_id")
    if data.get("scope") == "global":
        data["project_id"] = None
    for k, v in data.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id) -> bool:
    from app.models.agent import AgentRule

    rule = db.get(AgentRule, rule_id)
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


# ─── 生成插件管理（P5，对齐 TraeWork 插件）────────────

def _load_plugin_prompts(db: Session, plugin_names: list[str]) -> list[str]:
    """加载选中插件的执行约束（system prompt 注入用）。

    只取启用中的插件；名字不存在的静默忽略（不阻塞对话）。
    返回「插件label：prompt」列表。
    """
    from app.models.agent import AgentPlugin

    if not plugin_names:
        return []
    rows = list(
        db.scalars(
            select(AgentPlugin).where(
                AgentPlugin.name.in_(list(plugin_names)),
                AgentPlugin.enabled.is_(True),
            )
        ).all()
    )
    rows.sort(key=lambda r: r.sort)
    return [f"{r.label}（{r.name}）：{r.prompt}" for r in rows]


def list_plugins(db: Session, enabled_only: bool = False) -> list:
    from app.models.agent import AgentPlugin

    _ensure_builtin_plugins(db)
    q = select(AgentPlugin).order_by(AgentPlugin.sort.asc(), AgentPlugin.created_at.asc())
    if enabled_only:
        q = q.where(AgentPlugin.enabled.is_(True))
    return list(db.scalars(q).all())


def create_plugin(db: Session, payload) -> object:
    from app.models.agent import AgentPlugin

    name = payload.name.strip()
    if not name:
        raise ValueError("插件标识不能为空")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("插件标识需为小写字母/数字/下划线，且以字母开头（如 t2i、multi_ref）")
    if db.scalar(select(AgentPlugin).where(AgentPlugin.name == name)):
        raise ValueError(f"插件「{name}」已存在")
    if payload.mode not in _PLUGIN_MODES:
        raise ValueError(f"执行模式不合法：{payload.mode}（可选 {_PLUGIN_MODES}）")
    plugin = AgentPlugin(
        name=name,
        label=(payload.label or "").strip() or name,
        description=(payload.description or "").strip(),
        prompt=payload.prompt.strip(),
        tool=payload.tool.strip(),
        mode=payload.mode,
        enabled=payload.enabled,
        sort=payload.sort,
    )
    db.add(plugin)
    db.commit()
    db.refresh(plugin)
    return plugin


def update_plugin(db: Session, plugin_id, payload) -> object:
    from app.models.agent import AgentPlugin

    plugin = db.get(AgentPlugin, plugin_id)
    if not plugin:
        raise ValueError("插件不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("mode") and data["mode"] not in _PLUGIN_MODES:
        raise ValueError(f"执行模式不合法：{data['mode']}（可选 {_PLUGIN_MODES}）")
    for k, v in data.items():
        setattr(plugin, k, v)
    db.commit()
    db.refresh(plugin)
    return plugin


def delete_plugin(db: Session, plugin_id) -> bool:
    from app.models.agent import AgentPlugin

    plugin = db.get(AgentPlugin, plugin_id)
    if not plugin:
        return False
    db.delete(plugin)
    db.commit()
    return True


# ─── MCP 服务器配置 ──────────────────────────────────

def list_mcp_servers(db: Session, enabled_only: bool = False) -> list:
    from app.models.agent import AgentMcpServer

    q = select(AgentMcpServer).order_by(AgentMcpServer.sort.asc(), AgentMcpServer.created_at.asc())
    if enabled_only:
        q = q.where(AgentMcpServer.enabled.is_(True))
    return list(db.scalars(q).all())


_MCP_COMMAND_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _validate_mcp_command(command: str) -> str:
    """校验 MCP stdio 启动命令：仅允许简单可执行文件名，禁止路径/空格/Shell 元字符。

    stdio_client 用 argv（非 shell）启动，但 command 若为任意绝对路径（如 /bin/rm）
    配合 args 可执行任意程序。限制为常见 MCP 启动器（npx/uvx/python 等）的裸命令名，
    阻断路径注入与直接执行任意二进制。
    """
    command = (command or "").strip()
    if not command:
        raise ValueError("MCP 服务器 command 不能为空")
    if not _MCP_COMMAND_RE.fullmatch(command):
        raise ValueError(
            "MCP 服务器 command 仅支持可执行文件名（如 npx/uvx/python），"
            "不能包含路径、空格或特殊字符",
        )
    return command


def create_mcp_server(db: Session, payload) -> object:
    from app.models.agent import AgentMcpServer

    name = payload.name.strip()
    if not name:
        raise ValueError("MCP 服务器名称不能为空")
    if db.scalar(select(AgentMcpServer).where(AgentMcpServer.name == name)):
        raise ValueError(f"MCP 服务器「{name}」已存在")
    server = AgentMcpServer(
        name=name,
        description=(payload.description or "").strip(),
        command=_validate_mcp_command(payload.command),
        args=payload.args or [],
        env=payload.env,
        enabled=payload.enabled,
        sort=payload.sort,
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def update_mcp_server(db: Session, server_id, payload) -> object:
    from app.models.agent import AgentMcpServer

    server = db.get(AgentMcpServer, server_id)
    if not server:
        raise ValueError("MCP 服务器不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        name = data["name"].strip()
        dup = db.scalar(
            select(AgentMcpServer).where(AgentMcpServer.name == name, AgentMcpServer.id != server_id)
        )
        if dup:
            raise ValueError(f"MCP 服务器「{name}」已存在")
        data["name"] = name
    if data.get("command"):
        data["command"] = _validate_mcp_command(data["command"])
    for k, v in data.items():
        setattr(server, k, v)
    db.commit()
    db.refresh(server)
    return server


def delete_mcp_server(db: Session, server_id) -> bool:
    from app.models.agent import AgentMcpServer

    server = db.get(AgentMcpServer, server_id)
    if not server:
        return False
    db.delete(server)
    db.commit()
    return True


def test_mcp_server(db: Session, server_id) -> dict:
    """连接测试：发现服务器工具。成功返回 {ok, tools, message}，失败返回 {ok: False, message}。"""
    from app.models.agent import AgentMcpServer
    from app.services import mcp_service

    server = db.get(AgentMcpServer, server_id)
    if not server:
        raise ValueError("MCP 服务器不存在")
    try:
        tools = mcp_service.discover_tools(server, force_refresh=True)
    except ValueError as e:
        return {"ok": False, "message": str(e), "tools": []}
    names = [t["function"]["name"] for t in tools]
    return {
        "ok": True,
        "message": f"连接成功，发现 {len(tools)} 个工具：{'、'.join(names) or '无'}",
        "tools": tools,
    }


# ─── 创作角色配置（可配置创作角色/子智能体）──────────

def _load_role_configs(db: Session) -> dict:
    """加载全部启用角色：DB 自定义角色 + 内置兜底（DB 同名覆盖内置）。

    返回 {role_key: {"name", "prompt", "tools", "kind"}}。
    """
    from app.models.agent import AgentRoleConfig

    roles: dict = {}
    for r in db.scalars(
        select(AgentRoleConfig).where(AgentRoleConfig.enabled.is_(True))
    ).all():
        roles[r.role_key] = {
            "name": r.name,
            "prompt": r.persona,
            "tools": r.tools,
            "kind": r.kind,
        }
    for key, cfg in _AGENT_ROLES.items():
        roles.setdefault(key, {**cfg, "kind": "chat"})
    return roles


def list_roles(db: Session) -> list:
    from app.models.agent import AgentRoleConfig

    return list(
        db.scalars(
            select(AgentRoleConfig).order_by(
                AgentRoleConfig.sort.asc(), AgentRoleConfig.created_at.asc()
            )
        ).all()
    )


def create_role(db: Session, payload) -> "AgentRoleConfig":
    from app.models.agent import AgentRoleConfig

    role_key = (payload.role_key or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,32}", role_key):
        raise ValueError("role_key 仅支持小写字母/数字/下划线（2~32 位）")
    name = (payload.name or "").strip()
    if not name:
        raise ValueError("角色名不能为空")
    if db.scalar(select(AgentRoleConfig).where(AgentRoleConfig.role_key == role_key)):
        raise ValueError("该 role_key 已存在")
    if db.scalar(select(AgentRoleConfig).where(AgentRoleConfig.name == name)):
        raise ValueError("角色名已存在")
    if not (payload.persona or "").strip():
        raise ValueError("角色人设不能为空")
    r = AgentRoleConfig(
        name=name[:100],
        role_key=role_key,
        kind=payload.kind if payload.kind in ("chat", "subagent") else "chat",
        persona=(payload.persona or "").strip(),
        tools=payload.tools,
        enabled=payload.enabled,
        sort=payload.sort,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def update_role(db: Session, role_id, payload) -> "AgentRoleConfig":
    from app.models.agent import AgentRoleConfig

    r = db.get(AgentRoleConfig, role_id)
    if not r:
        raise ValueError("角色不存在")
    if payload.name is not None and (payload.name or "").strip():
        r.name = payload.name.strip()[:100]
    if payload.kind in ("chat", "subagent"):
        r.kind = payload.kind
    if payload.persona is not None and (payload.persona or "").strip():
        r.persona = payload.persona.strip()
    if payload.tools is not None:
        r.tools = payload.tools
    if payload.enabled is not None:
        r.enabled = payload.enabled
    if payload.sort is not None:
        r.sort = payload.sort
    db.commit()
    db.refresh(r)
    return r


def delete_role(db: Session, role_id) -> bool:
    from app.models.agent import AgentRoleConfig

    r = db.get(AgentRoleConfig, role_id)
    if not r:
        return False
    db.delete(r)
    db.commit()
    return True
