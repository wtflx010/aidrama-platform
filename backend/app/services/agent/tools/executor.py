"""工具注册层 · 执行器：注册表分发（替代原 `_execute_tool` 的 422 行 if 链）。

重构核心收益点：每个工具族在 `REGISTRY` 中注册自己的处理器，`execute_tool`
只做「开关校验 → 注册表查 handler → 执行 → 事件包装」。行为与原实现逐一对齐：
- 受控工具（terminal/file/code/browser）执行时二次校验 `.env` 开关
- pending 确认类（git_commit/git_push/create_project/project_delete）回传确认载荷
- 返回 (result dict, events list)，由 chat_stream 统一 emit

从 agent_service.py 剥离（原行号 2838~3239 _execute_tool + 2778~2828
_save_tool_message + 745~819 开关/动态加载），逻辑按名称注册未改动。
"""

import json
import logging
import re
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentMessage
from app.services.agent.engine.constants import _CHAT_TOTAL_TIMEOUT

logger = logging.getLogger(__name__)


# ── 工具开关（.env 能力开关）────────────────────────────

def _is_builtin_tool_enabled(tool_name: str) -> bool:
    """内置工具能力开关：terminal/file/browser 工具按 .env 配置决定是否注册给模型。"""
    from app.config import settings

    if tool_name == "terminal_execute":
        return settings.agent_terminal_enabled
    if tool_name in ("file_read", "file_write"):
        return settings.agent_file_enabled
    if tool_name.startswith(("code_", "git_")):
        return settings.agent_code_enabled
    if tool_name.startswith("browser_"):
        return settings.agent_browser_enabled
    return True


def _parse_args(arguments: str) -> dict:
    import json
    try:
        data = json.loads(arguments or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


# ── 工具消息落库（占位+回填 / pending 确认类）────────────

def _save_tool_message(db: Session, session_id, name: str, result: dict, msg_id=None) -> None:
    """持久化工具执行消息（含产出媒体/项目 id，供前端渲染卡片）。

    msg_id 非空时更新该条消息（工具执行开始前已落库 running 占位，完成后回填结果）；
    为空时新增。占位+回填机制保证切走/切回会话时，执行中的工具消息不丢失。
    """
    # pending 确认类工具（git_commit/git_push/create_project 草案/project_delete）：等待用户确认才算完成，
    # 持久化为 pending 而非 succeeded，历史回放仍展示确认卡
    if (
        result.get("pending_git_commit")
        or result.get("pending_git_push")
        or result.get("pending_project_draft")
        or result.get("pending_project_delete")
    ):
        tool_status = "pending"
    else:
        tool_status = "succeeded" if result.get("ok") else "failed"
    tool_params = result.get("params")
    # create_project 草案 / project_delete 确认数据随 tool_params 持久化，切走/回放后确认卡仍可操作
    if result.get("pending_project_draft"):
        tool_params = {**(tool_params or {}), "pending_project_draft": result["pending_project_draft"]}
    if result.get("pending_project_delete"):
        tool_params = {**(tool_params or {}), "pending_project_delete": result["pending_project_delete"]}
    if msg_id is not None:
        msg = db.get(AgentMessage, msg_id)
        if msg is not None:
            msg.tool_status = tool_status
            msg.tool_params = tool_params
            msg.content = result.get("message", "")
            if result.get("media_urls"):
                msg.media_urls = result.get("media_urls")
            if result.get("project_id"):
                msg.project_id = result.get("project_id")
            db.commit()
            return
    msg = AgentMessage(
        session_id=session_id,
        role="tool",
        tool_name=name,
        tool_params=tool_params,
        tool_status=tool_status,
        content=result.get("message", ""),
        media_urls=result.get("media_urls"),
        project_id=result.get("project_id"),
    )
    db.add(msg)
    db.commit()


# ── 动态工具加载（内置开关过滤 + Skill + MCP）────────────

def _load_dynamic_tools(db: Session) -> list[dict]:
    """构建对话可用的全部工具：仅内置 _TOOLS（当前能力边界 = 写剧本单一工具）。

    2026-08-23：不再注入 Skill / MCP 动态工具，其他一切能力从工具侧移除。
    """
    from app.models.agent import AgentMcpServer, AgentSkill
    from app.services.agent.tools.specs import _TOOLS

    # 内置工具按能力开关过滤（terminal/file/browser 可在 .env 关闭）
    tools = [t for t in _TOOLS if _is_builtin_tool_enabled(t["function"]["name"])]
    # 2026-08-23 能力边界：仅保留内置 _TOOLS（写剧本），不再注入 Skill / MCP 动态工具（H3 分镜规范已内置到写剧本提示词）
    return tools


# ── 工具处理器注册表 ──────────────────────────────────
# handler 签名：handler(db, session_id, args, ref_images, model, timeout_budget)
#   → (result dict, events list)
# 引用 ctx 中变量通过闭包；为避免与旧实现产生行为差异，所有分支体原样移植。

_REGISTRY: dict[str, Callable[..., tuple[dict, list[dict]]]] = {}


def _register(name: str):
    def deco(fn: Callable):
        _REGISTRY[name] = fn
        return fn
    return deco


# ── 生图 ─────────────────────────────────────────────

@_register("generate_image")
def _h_generate_image(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.media import _tool_generate_image

    events: list[dict] = [{"type": "tool", "name": "generate_image", "status": "running", "step": "提交生成任务"}]
    url = _tool_generate_image(db, session_id, args, ref_images or [], events, timeout_budget=timeout_budget)
    result = {"ok": True, "params": args, "media_urls": [url], "message": f"图片已生成：{url}"}
    events.append({"type": "media", "kind": "image", "url": url})
    return result, events


@_register("generate_video")
def _h_generate_video(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.media import _tool_generate_video

    draft_id, task_id = _tool_generate_video(db, args, refs=ref_images or None)
    result = {
        "ok": True,
        "params": {**args, "draft_id": draft_id, "task_id": task_id},
        "message": f"视频任务已提交（draft_id={draft_id}），生成中请稍候",
    }
    events: list[dict] = [{
        "type": "tool", "name": "generate_video", "status": "running",
        "draft_id": draft_id, "task_id": task_id,
    }]
    return result, events


@_register("create_project")
def _h_create_project(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.project import _tool_create_project_draft

    events: list[dict] = [{"type": "tool", "name": "create_project", "status": "running", "step": "整理剧本内容…"}]
    draft_payload = _tool_create_project_draft(db, session_id, args, model)
    result = {
        "ok": True,
        "message": f"已生成项目草案（{draft_payload['summary']}），等待确认…",
        "pending_project_draft": draft_payload,
    }
    events.append({
        "type": "tool", "name": "create_project", "status": "pending",
        "step": "等待确认项目（内容 / 风格 / 画幅）",
        "pending_project_draft": draft_payload,
    })
    return result, events


@_register("project_list")
def _h_project_list(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.project_service import list_projects

    events: list[dict] = [{"type": "tool", "name": "project_list", "status": "running", "step": "查询项目列表…"}]
    rows = list_projects(db)
    if not rows:
        result = {"ok": True, "params": args, "message": "当前没有任何项目。"}
    else:
        lines = [
            f"- {p.title}（id={p.id}，状态={p.status}，创建于 {p.created_at.strftime('%Y-%m-%d %H:%M')}）"
            for p in rows
        ]
        result = {"ok": True, "params": args, "message": f"共 {len(rows)} 个项目：\n" + "\n".join(lines)}
    return result, events


@_register("project_delete")
def _h_project_delete(db, session_id, args, ref_images, model, timeout_budget):
    from app.models.project import Project

    events: list[dict] = []
    # 破坏性操作：先解析目标项目返回待确认载荷，前端展示确认卡后调 /agent/projects/delete 真正执行
    pid = (args.get("project_id") or "").strip()
    if not pid:
        raise ValueError("缺少 project_id 参数，请先调用 project_list 获取项目 id")
    # 先按 UUID 精确匹配，再按名称/名称片段兜底解析（模型可能只拿到名称）
    from uuid import UUID as _UUID
    p = None
    try:
        p = db.get(Project, _UUID(pid))
    except Exception:
        p = None
    if p is None:
        p = db.scalar(
            select(Project).where(Project.title == pid).order_by(Project.created_at.desc())
        )
    if p is None and len(pid) >= 2:
        p = db.scalar(
            select(Project).where(Project.title.ilike(f"%{pid}%")).order_by(Project.created_at.desc())
        )
    if p is None:
        raise ValueError(f"未找到项目「{pid}」，请先调用 project_list 确认项目名称与 id")
    from app.models.segment import Segment
    from app.models.asset import Asset
    from app.models.project import Episode
    from app.models.episode_video import EpisodeVideo
    from sqlalchemy import func as _func
    ep_ids = [e.id for e in db.scalars(select(Episode).where(Episode.project_id == p.id)).all()]
    seg_cnt = db.scalar(select(_func.count()).select_from(Segment).where(Segment.episode_id.in_(ep_ids))) if ep_ids else 0
    asset_cnt = db.scalar(select(_func.count()).select_from(Asset).where(Asset.project_id == p.id)) or 0
    video_cnt = db.scalar(
        select(_func.count()).select_from(EpisodeVideo).where(EpisodeVideo.episode_id.in_(ep_ids))
    ) if ep_ids else 0
    detail = f"{len(ep_ids)} 幕 / {seg_cnt} 分镜 / {asset_cnt} 资产 / {video_cnt} 视频"
    payload = {"project_id": str(p.id), "title": p.title, "detail": detail}
    result = {
        "ok": True,
        "message": f"等待用户确认删除项目「{p.title}」（{detail}）…",
        "pending_project_delete": payload,
    }
    events.append({
        "type": "tool", "name": "project_delete", "status": "pending",
        "step": "等待用户确认删除项目",
        "pending_project_delete": payload,
    })
    return result, events


# ── 联网/搜索 ────────────────────────────────────────

@_register("web_search")
def _h_web_search(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.web import _format_search_results, _tool_web_search

    events: list[dict] = [{"type": "tool", "name": "web_search", "status": "running"}]
    results = _tool_web_search(args)
    text = _format_search_results(results)
    return {"ok": True, "params": args, "message": text}, events


@_register("github_search")
def _h_github_search(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.web import _format_github_results, _tool_github_search

    events: list[dict] = [{"type": "tool", "name": "github_search", "status": "running", "step": "搜索 GitHub 仓库…"}]
    results = _tool_github_search(args)
    text = _format_github_results(results)
    return {"ok": True, "params": args, "message": text}, events


@_register("web_fetch")
def _h_web_fetch(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.web import _tool_web_fetch

    events: list[dict] = [{"type": "tool", "name": "web_fetch", "status": "running", "step": "读取网页内容…"}]
    text = _tool_web_fetch(args)
    return {"ok": True, "params": args, "message": text}, events


# ── Skill 工具 ───────────────────────────────────────

@_register("install_skill")
def _h_install_skill(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.skill import _tool_install_skill

    events: list[dict] = [{"type": "tool", "name": "install_skill", "status": "running", "step": "安装技能中…"}]
    result_text = _tool_install_skill(db, args)
    return {"ok": True, "params": args, "message": result_text}, events


@_register("skill_list")
def _h_skill_list(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.skill import _tool_skill_list

    events: list[dict] = [{"type": "tool", "name": "skill_list", "status": "running", "step": "加载技能清单…"}]
    result_text = _tool_skill_list(db)
    return {"ok": True, "params": args, "message": result_text}, events


@_register("skill_view")
def _h_skill_view(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.skill import _tool_skill_view

    events: list[dict] = [{"type": "tool", "name": "skill_view", "status": "running", "step": "加载技能内容…"}]
    result_text = _tool_skill_view(db, args)
    return {"ok": True, "params": args, "message": result_text}, events


# ── 终端/文件 ────────────────────────────────────────

@_register("terminal_execute")
def _h_terminal(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.shell import _tool_terminal_execute

    events: list[dict] = [{"type": "tool", "name": "terminal_execute", "status": "running", "step": "执行终端命令…"}]
    result_text = _tool_terminal_execute(args)
    return {"ok": True, "params": args, "message": result_text}, events


@_register("file_read")
def _h_file_read(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.shell import _tool_file_read

    events: list[dict] = [{"type": "tool", "name": "file_read", "status": "running", "step": "读取文件…"}]
    result_text = _tool_file_read(args)
    return {"ok": True, "params": args, "message": result_text}, events


@_register("file_write")
def _h_file_write(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.shell import _tool_file_write

    events: list[dict] = [{"type": "tool", "name": "file_write", "status": "running", "step": "写入文件…"}]
    result_text = _tool_file_write(args)
    return {"ok": True, "params": args, "message": result_text}, events


# ── code 工具族 ──────────────────────────────────────

def _register_code_family():
    """把 code_* 工具族注册进注册表（独立注册避免循环 import）。"""
    from app.services.agent.tools.code import _TOOL_CODE_DISPATCH
    _CODE_STEPS = {
        "code_search": "搜索代码…",
        "code_list": "列出文件…",
        "code_read": "读取文件…",
        "code_edit": "编辑文件…",
        "code_write": "写入文件…",
        "code_execute": "沙箱执行代码…",
        "code_diagnose": "静态诊断…",
        "code_rollback": "回滚检查点…",
    }
    for name, fn in _TOOL_CODE_DISPATCH.items():
        def make_handler(fn=fn, name=name):
            def handler(db, session_id, args, ref_images, model, timeout_budget):
                events: list[dict] = [{
                    "type": "tool", "name": name, "status": "running",
                    "step": _CODE_STEPS.get(name, "编码操作中…"),
                }]
                result_text = fn(args)
                return {"ok": True, "params": args, "message": result_text}, events
            return handler
        _REGISTRY[name] = make_handler()


_register_code_family()


# ── git 工具族 ───────────────────────────────────────

@_register("git_status")
def _h_git_status(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.git import _tool_git_status

    events: list[dict] = [{"type": "tool", "name": "git_status", "status": "running", "step": "查看 git 状态…"}]
    return {"ok": True, "params": args, "message": _tool_git_status(args)}, events


@_register("git_diff")
def _h_git_diff(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.git import _tool_git_diff

    events: list[dict] = [{"type": "tool", "name": "git_diff", "status": "running", "step": "查看改动 diff…"}]
    return {"ok": True, "params": args, "message": _tool_git_diff(args)}, events


@_register("git_commit")
def _h_git_commit(db, session_id, args, ref_images, model, timeout_budget):
    # 破坏性操作：先返回待确认状态，前端展示确认按钮后调 /agent/git/commit 真正执行
    files = args.get("files") or []
    message = (args.get("message") or "").strip()
    if not message:
        raise ValueError("提交信息 message 不能为空")
    result = {
        "ok": True,
        "message": f"等待用户确认提交 {len(files) if files else '全部'} 个文件…",
        "pending_git_commit": {"files": list(files), "message": message},
    }
    events: list[dict] = [{
        "type": "tool", "name": "git_commit", "status": "pending",
        "step": "等待用户确认提交",
        "pending_git_commit": {"files": list(files), "message": message},
    }]
    return result, events


@_register("git_log")
def _h_git_log(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.git import _tool_git_log

    events: list[dict] = [{"type": "tool", "name": "git_log", "status": "running", "step": "查看提交历史…"}]
    return {"ok": True, "params": args, "message": _tool_git_log(args)}, events


@_register("git_branch")
def _h_git_branch(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.tools.git import _tool_git_branch

    events: list[dict] = [{"type": "tool", "name": "git_branch", "status": "running", "step": "查看分支列表…"}]
    return {"ok": True, "params": args, "message": _tool_git_branch(args)}, events


@_register("git_push")
def _h_git_push(db, session_id, args, ref_images, model, timeout_budget):
    # 破坏性操作：先返回待确认状态，前端展示确认按钮后调 /agent/git/push 真正执行
    remote = args.get("remote") or "origin"
    branch = args.get("branch") or ""
    result = {
        "ok": True,
        "message": f"等待用户确认推送到 {remote}/{branch or '当前分支'}…",
        "pending_git_push": {"remote": remote, "branch": branch},
    }
    events: list[dict] = [{
        "type": "tool", "name": "git_push", "status": "pending",
        "step": "等待用户确认推送",
        "pending_git_push": {"remote": remote, "branch": branch},
    }]
    return result, events


# ── TTS ──────────────────────────────────────────────

@_register("tts_speak")
def _h_tts_speak(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.media import _tool_tts_speak

    events: list[dict] = [{"type": "tool", "name": "tts_speak", "status": "running", "step": "准备朗读…"}]
    url = _tool_tts_speak(db, args, events)
    result = {"ok": True, "params": args, "media_urls": [url], "message": f"语音已生成：{url}"}
    events.append({"type": "media", "kind": "audio", "url": url, "name": "tts_speak"})
    return result, events


# ── 画布（生图工作台）────────────────────────────────

@_register("canvas_list")
def _h_canvas_list(db, session_id, args, ref_images, model, timeout_budget):
    from uuid import UUID as _UUID

    from app.services import canvas_service

    events: list[dict] = [{"type": "tool", "name": "canvas_list", "status": "running", "step": "查询画布列表…"}]
    pid = (args.get("project_id") or "").strip() or None
    pid_u = _UUID(pid) if pid else None
    rows = canvas_service.list_boards(db, project_id=pid_u)
    if not rows:
        return {"ok": True, "params": args, "message": "当前没有任何画布。"}, events
    lines = [
        f"- {b.name}（id={b.id}，v{b.version}，节点数 {len((b.document or {}).get('nodes', []))}）"
        for b in rows
    ]
    return {"ok": True, "params": args, "message": f"共 {len(rows)} 张画布：\n" + chr(10).join(lines)}, events


def _canvas_board_handler(name: str):
    """生成以 board_id 为前置校验的画布工具 handler。"""
    def handler(db, session_id, args, ref_images, model, timeout_budget):
        from uuid import UUID as _UUID

        from app.schemas.canvas import CanvasBoardGenerate
        from app.services import canvas_service

        board_id = (args.get("board_id") or "").strip()
        if not board_id:
            raise ValueError("缺少 board_id 参数，请先调用 canvas_list 获取画布 id")
        try:
            board_uuid = _UUID(board_id)
        except ValueError:
            raise ValueError(f"board_id 不是有效 UUID：{board_id}")
        if name == "canvas_get":
            events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": "读取画布…"}]
            board = canvas_service.get(db, board_uuid)
            doc = board.document or {}
            shots = []
            for n in doc.get("nodes", []):
                if n.get("type") != "shot":
                    continue
                data = n.get("data") or {}
                shots.append(
                    f"- 节点 {n.get('id')}（分镜 {data.get('segmentId')}，状态 {data.get('status') or 'idle'}）"
                    f"提示词：{(data.get('prompt') or '')[:100]}"
                )
            head = f"画布「{board.name}」（id={board.id}，v{board.version}）共 {len(shots)} 个分镜节点："
            msg = (head + chr(10) + chr(10).join(shots)) if shots else head + "（无分镜节点，请先导入分镜）"
            return {"ok": True, "params": args, "message": msg}, events
        if name == "canvas_import_segments":
            events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": "导入分镜到画布…"}]
            seg_ids = args.get("segment_ids") or []
            if not seg_ids:
                raise ValueError("缺少 segment_ids（至少一个分镜 id）")
            board = canvas_service.import_segments(
                db, [_UUID(str(s)) for s in seg_ids], (args.get("name") or "").strip() or None
            )
            n = len((board.document or {}).get("nodes", []))
            return {
                "ok": True,
                "message": f"画布已创建：{board.name}（board_id={board.id}，{n} 个节点）",
                "board_id": str(board.id),
            }, events
        if name == "canvas_generate":
            events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": "提交画布批量生成…"}]
            board_ = canvas_service.get(db, board_uuid)
            doc = board_.document or {}
            node_ids = args.get("node_ids") or None
            if not node_ids:
                node_ids = [n["id"] for n in doc.get("nodes", []) if n.get("type") == "shot"]
            if not node_ids:
                raise ValueError("画布没有可生成的节点（请先导入分镜）")
            task, results = canvas_service.generate(
                db, board_uuid,
                CanvasBoardGenerate(node_ids=node_ids, ratio=args.get("ratio"), kind=(args.get("kind") or "image")),
            )
            ok_cnt = sum(1 for r in results if not r.get("error"))
            errs = [r["error"] for r in results if r.get("error")]
            msg = f"画布生成任务已提交（task_id={task.id}，节点 {ok_cnt}/{len(results)}）"
            if errs:
                msg += "；跳过：" + "；".join(errs[:3])
            return {"ok": True, "message": msg, "task_id": str(task.id)}, events
        if name == "canvas_audit":
            events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": "画布体检…"}]
            import json as _json
            data = canvas_service.audit_board(db, board_uuid)
            msg = _json.dumps(data, ensure_ascii=False, default=str)
            return {"ok": True, "params": args, "message": msg}, events
        raise ValueError(f"未知画布工具：{name}")
    return handler


_REGISTRY["canvas_get"] = _canvas_board_handler("canvas_get")
_REGISTRY["canvas_import_segments"] = _canvas_board_handler("canvas_import_segments")
_REGISTRY["canvas_generate"] = _canvas_board_handler("canvas_generate")
_REGISTRY["canvas_audit"] = _canvas_board_handler("canvas_audit")


# ── 浏览器 ───────────────────────────────────────────

def _register_browser_family():
    from app.services import browser_service

    for action in ("navigate", "snapshot", "click", "type", "screenshot"):
        name = f"browser_{action}"

        def make_handler(action=action, name=name):
            def handler(db, session_id, args, ref_images, model, timeout_budget):
                events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": "浏览器操作中…"}]
                result = browser_service.run(action, args)
                if not result.get("ok"):
                    raise ValueError(result.get("text") or "浏览器操作失败")
                base: dict[str, Any] = {"ok": True, "params": args, "message": result["text"]}
                if result.get("url"):
                    base.update({"media_urls": [result["url"]]})
                    events.append({"type": "media", "kind": "image", "url": result["url"], "name": name})
                return base, events
            return handler
        _REGISTRY[name] = make_handler()


_register_browser_family()


# ── 子智能体 ─────────────────────────────────────────
# 2026-08-23：按需求停用「角色子智能体」——写剧本/分镜/美术由主智能体直接完成，
# 不再调度 编剧/导演/美术 独立角色。注册与工具目录已移除（subagent.py 保留备用）。


# ── 写小说 / 写剧本（四阶段确认流）──────────────────────

def _read_script_outline_state(db, session_id):
    """读取会话「当前确认版集纲」（plan_script_outline 写入创作状态卡）。"""
    from app.models.agent import AgentCreativeState

    row = db.scalars(
        select(AgentCreativeState).where(
            AgentCreativeState.session_id == session_id,
            AgentCreativeState.role_key == "script_outline",
        )
    ).first()
    if row is None or not row.content:
        return None
    try:
        d = json.loads(row.content)
    except Exception:
        return None
    return d if isinstance(d, dict) and (d.get("episodes") or []) else None


@_register("plan_script_outline")
def _h_plan_script_outline(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.writing import _tool_plan_script_outline

    events = [{"type": "tool", "name": "plan_script_outline", "status": "running", "step": "生成集纲预览…"}]
    result = _tool_plan_script_outline(db, session_id, args, model)
    events.append({"type": "tool", "name": "plan_script_outline", "status": "succeeded"})
    return result, events


@_register("generate_shot_preview")
def _h_generate_shot_preview(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.writing import _tool_generate_shot_preview

    events = [{"type": "tool", "name": "generate_shot_preview", "status": "running", "step": "按已确认剧本生成分镜预览…"}]
    message = _tool_generate_shot_preview(db, args, model)
    result = {"ok": True, "params": args, "message": message}
    events.append({"type": "tool", "name": "generate_shot_preview", "status": "succeeded"})
    return result, events


@_register("generate_poster_and_project")
def _h_generate_poster_and_project(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.writing import _tool_generate_poster_and_project

    events = [{"type": "tool", "name": "generate_poster_and_project", "status": "running", "step": "生成封面并创建项目…"}]
    message = _tool_generate_poster_and_project(db, args, model)
    result = {"ok": True, "params": args, "message": message}
    events.append({"type": "tool", "name": "generate_poster_and_project", "status": "succeeded"})
    return result, events


@_register("write_novel")
def _h_write_novel(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.writing import _tool_write_novel

    events: list[dict] = [{"type": "tool", "name": "write_novel", "status": "running", "step": "提交长篇写作任务"}]
    novel_id, task_id, title = _tool_write_novel(db, args, model=model)
    result = {
        "ok": True,
        "params": {**args, "novel_id": str(novel_id), "task_id": str(task_id)},
        # message 带上 novel_id，模型后续轮次可凭它调用 write_novel 续写
        "message": f"长篇小说《{title}》写作任务已提交（novel_id={novel_id}，task_id={task_id}），后台逐章生成中，完成前可先做别的事",
    }
    events: list[dict] = [{
        "type": "tool", "name": "write_novel", "status": "running",
        "task_id": str(task_id), "novel_id": str(novel_id),
        "step": "已提交，后台逐章写作中…",
    }]
    return result, events


@_register("write_script")
def _h_write_script(db, session_id, args, ref_images, model, timeout_budget):
    from app.services.agent.creative.writing import _tool_write_script, _script_outline_confirmed

    # 四阶段确认流·大纲确认门槛：写剧本必须先「plan_script_outline → 用户确认」。
    # 直写路径（full_script，对话里已细化并确认过完整正文）例外；其余一律强制。
    if not (args.get("full_script") or "").strip():
        outline = _read_script_outline_state(db, session_id)
        if not outline:
            raise ValueError(
                "写剧本前必须先确认剧本大纲：请调用 plan_script_outline 生成集纲并原样展示给用户，"
                "等用户回复确认（如「没问题 / 确认 / 可以」）后再调用 write_script。"
            )
        if not args.get("confirm_outline"):
            raise ValueError(
                "剧本大纲尚未获得用户确认：请把上一轮生成的集纲原样展示给用户，"
                "等用户回复确认后再调用 write_script（需带 confirm_outline=true）。"
            )
        if not _script_outline_confirmed(db, session_id):
            raise ValueError(
                "未检测到用户对大纲的确认：请勿在同一个请求里连续调用工具（写剧本/分镜/封面必须分步确认）。"
                "先把 plan_script_outline 生成的集纲展示给用户，等用户在下一轮回复确认（如「没问题」）后再调用 write_script。"
            )
        # 以状态卡里的确认为准注入大纲（模型转述参数不可信），后台任务据此逐集写作
        args = {**args, "outline": outline}

    events: list[dict] = [{"type": "tool", "name": "write_script", "status": "running", "step": "提交剧本写作任务"}]
    novel_id, task_id, title = _tool_write_script(db, args, model=model)
    result = {
        "ok": True,
        "params": {**args, "novel_id": str(novel_id), "task_id": str(task_id)},
        "message": f"剧本《{title}》写作任务已提交（novel_id={novel_id}，task_id={task_id}），后台逐集写入剧本库，完成后可在剧本库「分析 → 生成项目」",
    }
    events: list[dict] = [{
        "type": "tool", "name": "write_script", "status": "running",
        "task_id": str(task_id), "novel_id": str(novel_id),
        "step": "已提交，后台逐集写入剧本库…",
    }]
    return result, events


# ── 统一执行入口 ─────────────────────────────────────

def execute_tool(
    db: Session, session_id, name: str, arguments: str,
    ref_images: list[str] | None = None, model=None,
    timeout_budget: int = _CHAT_TOTAL_TIMEOUT,
) -> tuple[dict, list[dict]]:
    """执行工具，返回 (结果 dict, SSE 事件列表)。

    结果 dict：{ok, message, params, media_urls?, project_id?}
    事件列表由 chat_stream 统一 emit（本函数非生成器，无法直接 yield）。
    ref_images：本次请求携带的参考图（data URI / 媒体 URL），generate_image 用作图生图参考。
    model：当前对话模型（子智能体复用同一模型，避免不一致）。
    timeout_budget：本次请求剩余时间预算（秒），供同步长任务（生图轮询）遵守总超时。
    """
    # 受控工具（terminal/file/code/browser 等）在注册时按 .env 开关过滤；
    # 执行时二次校验，防止运行中配置变更导致越权执行
    if not _is_builtin_tool_enabled(name):
        raise ValueError(f"工具「{name}」已被系统配置禁用")
    args = _parse_args(arguments)

    # 静态注册表优先（内置工具、含 install_skill / skill_list / skill_view）
    handler = _REGISTRY.get(name)
    if handler is not None:
        return handler(db, session_id, args, ref_images or [], model, timeout_budget)

    # 动态工具：skill_<name> / mcp__<server>__<tool>（动态注册名，不在静态表）
    if name.startswith("skill_"):
        from app.services.agent.creative.subagent import _run_skill

        skill_name = name[len("skill_"):]
        events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": f"Skill「{skill_name}」执行中…"}]
        task = (args.get("task") or "").strip()
        if not task:
            raise ValueError("Skill 任务描述不能为空")
        result_text = _run_skill(db, skill_name, task, refs=ref_images, model=model)
        return {"ok": True, "params": args, "message": result_text}, events
    if name.startswith("mcp__"):
        from app.models.agent import AgentMcpServer
        from app.services import mcp_service

        rest = name[len("mcp__"):]
        server_name, _, tool_name = rest.partition("__")
        server = db.scalar(select(AgentMcpServer).where(AgentMcpServer.name == server_name))
        if not server:
            raise ValueError(f"MCP 服务器「{server_name}」不存在")
        events: list[dict] = [{"type": "tool", "name": name, "status": "running", "step": f"MCP「{server_name}」调用中…"}]
        result_text = mcp_service.call_tool(server, tool_name, args)
        return {"ok": True, "params": args, "message": result_text}, events

    # 动态前缀处理器（skill_/mcp__ 已在上方处理；此处仅防御未知名称）
    raise ValueError(f"未知工具：{name}")


# 兼容旧名（门面阶段可继续用 agent_service._execute_tool）
_execute_tool = execute_tool
