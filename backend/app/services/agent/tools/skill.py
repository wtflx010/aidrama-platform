"""工具注册层 · Skill 工具：list / view / install（渐进式披露）。

从 agent_service.py 剥离（原行号 5190~5270 区域），逻辑未改动。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session


def _tool_skill_list(db: Session) -> str:
    """渐进式披露第 1 层：列出启用中的全部 Skill（name + description 元数据）。

    供模型判断任务是否需要某技能；正文通过 skill_view 按需加载。
    """
    from app.models.agent import AgentSkill

    skills = list(
        db.scalars(
            select(AgentSkill)
            .where(AgentSkill.enabled.is_(True))
            .order_by(AgentSkill.tool_type.asc(), AgentSkill.sort.asc(), AgentSkill.created_at.asc())
        ).all()
    )
    if not skills:
        return "技能库为空，暂无可用 Skill。"
    lines = [f"技能库共 {len(skills)} 个可用 Skill："]
    for s in skills:
        kind = "可调用" if s.tool_type == "prompt" else ("内置工具" if s.tool_type == "builtin_tool" else "知识参考")
        lines.append(f"- {s.name}（{kind}）：{s.description}")
    lines.append("\n确定需要哪个后，调用 skill_view(name=技能名) 加载其完整内容。")
    return "\n".join(lines)


def _tool_skill_view(db: Session, args: dict) -> str:
    """渐进式披露第 2 层：加载指定 Skill 的完整内容（prompt/说明）。

    技能不存在、已停用或不可加载时返回明确提示，让模型换用其他技能或直接回答。
    """
    from app.models.agent import AgentSkill

    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("技能名称不能为空（先调用 skill_list 查看可用技能）")
    skill = db.scalar(select(AgentSkill).where(AgentSkill.name == name))
    if not skill:
        avail = _tool_skill_list(db).split("\n")[:12]
        raise ValueError(f"技能「{name}」不存在。可用技能：\n" + "\n".join(avail))
    if not skill.enabled:
        raise ValueError(f"技能「{name}」已停用，无法加载")
    kind = "可调用技能" if skill.tool_type == "prompt" else ("内置工具" if skill.tool_type == "builtin_tool" else "知识参考")
    head = f"技能「{skill.name}」（{kind}）\n用途：{skill.description}"
    body = skill.prompt or "（无正文）"
    return f"{head}\n\n【技能内容】\n{body}"[:8000]


def _tool_install_skill(db: Session, args: dict) -> str:
    """安装技能（模型自主调用）：校验并写入 AgentSkill，安装后注册为 skill_<name> 可调用工具。

    - name 仅允许小写英文+下划线（作为 skill_<name> 工具名）
    - tool_type 仅允许 prompt/knowledge（builtin_tool 需硬编码 handler，不允许模型安装）
    """
    import re as _re

    from app.models.agent import AgentSkill

    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    tool_type = (args.get("tool_type") or "prompt").strip()
    if not name or not description or not prompt:
        raise ValueError("技能安装参数不完整：name / description / prompt 均必填")
    if not _re.fullmatch(r"[a-z][a-z0-9_]{0,99}", name):
        raise ValueError("技能 name 需为小写英文+数字+下划线（如 minimax_h3_generation），请修正后重试")
    if tool_type not in ("prompt", "knowledge"):
        raise ValueError("tool_type 仅支持 prompt 或 knowledge")
    exists = db.scalar(select(AgentSkill).where(AgentSkill.name == name))
    if exists:
        return (
            f"技能「{name}」已存在于技能库（enabled={exists.enabled}）。"
            "如需覆盖更新，请说明修改点；或改用其他名称安装。"
        )
    skill = AgentSkill(
        name=name,
        description=description,
        prompt=prompt,
        tool_type=tool_type,
        enabled=True,
        sort=100,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return (
        f"技能「{name}」已安装成功（ID={skill.id}，tool_type={tool_type}，已启用）。\n"
        f"用途：{description}\n"
        f"安装后已注册为可调用工具 skill_{name}，后续对话中可直接让我执行该技能；"
        "也可在「Skill 管理」面板查看/编辑/停用。"
    )


def list_skills(db: Session, enabled_only: bool = False) -> list:
    from app.models.agent import AgentSkill

    q = select(AgentSkill).order_by(AgentSkill.sort.asc(), AgentSkill.created_at.asc())
    if enabled_only:
        q = q.where(AgentSkill.enabled.is_(True))
    return list(db.scalars(q).all())


def create_skill(db: Session, payload) -> object:
    from app.models.agent import AgentSkill

    name = payload.name.strip()
    if not name:
        raise ValueError("Skill 名称不能为空")
    if db.scalar(select(AgentSkill).where(AgentSkill.name == name)):
        raise ValueError(f"Skill「{name}」已存在")
    skill = AgentSkill(
        name=name,
        description=payload.description.strip(),
        prompt=payload.prompt.strip(),
        tool_type=payload.tool_type,
        handler=(payload.handler or "").strip() or None,
        enabled=payload.enabled,
        sort=payload.sort,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


def update_skill(db: Session, skill_id, payload) -> object:
    from app.models.agent import AgentSkill

    skill = db.get(AgentSkill, skill_id)
    if not skill:
        raise ValueError("Skill 不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("name"):
        name = data["name"].strip()
        dup = db.scalar(
            select(AgentSkill).where(AgentSkill.name == name, AgentSkill.id != skill_id)
        )
        if dup:
            raise ValueError(f"Skill「{name}」已存在")
        data["name"] = name
    if "handler" in data and data.get("handler"):
        data["handler"] = data["handler"].strip() or None
    for k, v in data.items():
        setattr(skill, k, v)
    db.commit()
    db.refresh(skill)
    return skill


def delete_skill(db: Session, skill_id) -> bool:
    from app.models.agent import AgentSkill

    skill = db.get(AgentSkill, skill_id)
    if not skill:
        return False
    db.delete(skill)
    db.commit()
    return True
