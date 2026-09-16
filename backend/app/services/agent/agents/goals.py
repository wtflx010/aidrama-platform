"""智能体工作流层 · 创作目标（Goal 工作流）。

从 agent_service.py 剥离（原行号 5922~6094 区域），逻辑未改动。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.agent.engine.constants import _MAX_HISTORY
from app.services.agent.engine.models import _resolve_chat_model
from app.services.agent.engine.parsing import _parse_json_flexible


def create_goal(db: Session, session_id, message: str, model_id=None) -> "AgentGoal":
    """设定创作目标：LLM 提炼目标标题 / 目标描述 / 完成标准，存库（status=active）。

    用户之后每点一次「推进」，AI 基于当前进度执行一步并自评是否达成。
    """
    from app.models.agent import AgentGoal, AgentSession
    from app.providers.registry import ProviderRegistry

    session = db.get(AgentSession, session_id)
    if not session:
        raise ValueError("会话不存在")
    message = (message or "").strip()
    if not message:
        raise ValueError("目标描述不能为空")
    model = _resolve_chat_model(db, model_id)
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是创作目标规划师。把用户的目标描述提炼为可自动推进、可验收的创作目标。"
                    "必须输出严格 JSON：\n"
                    '{"title": "目标标题(≤20字)", "goal": "完整目标描述（含范围/数量/质量要求）", '
                    '"criteria": "完成标准（可验证的验收点，如 12 集大纲齐全、每集含钩子）"}\n'
                    "不要输出 JSON 之外的任何文字。"
                ),
            },
            {"role": "user", "content": message},
        ]
    )
    raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _parse_json_flexible(raw)
    if not data or not data.get("goal"):
        raise ValueError("目标设定失败，请重试")
    goal = AgentGoal(
        session_id=session.id,
        title=(data.get("title") or "创作目标")[:200],
        goal=data.get("goal") or message,
        criteria=data.get("criteria") or "",
        status="active",
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def get_goal(db: Session, goal_id) -> "AgentGoal":
    from app.models.agent import AgentGoal

    g = db.get(AgentGoal, goal_id)
    if not g:
        raise ValueError("目标不存在")
    return g


def list_goals(db: Session, session_id) -> list:
    from app.models.agent import AgentGoal

    return list(
        db.scalars(
            select(AgentGoal)
            .where(AgentGoal.session_id == session_id)
            .order_by(AgentGoal.created_at.asc())
        ).all()
    )


def set_goal_status(db: Session, goal_id, status: str) -> "AgentGoal":
    """更新目标状态：active / paused / done。"""
    from app.models.agent import AgentGoal

    # 2026-08-15：状态白名单校验（此前任意字符串可写入，前端状态机错乱）
    if status not in ("active", "paused", "done"):
        raise ValueError("目标状态仅支持：active / paused / done")

    g = db.get(AgentGoal, goal_id)
    if not g:
        raise ValueError("目标不存在")
    g.status = status
    db.commit()
    db.refresh(g)
    return g


def evaluate_goal(db: Session, goal_id, model_id=None) -> "AgentGoal":
    """Goal 自评：基于目标与当前会话最新进展，判断是否达成；未达成则更新进度摘要。"""
    from app.models.agent import AgentGoal
    from app.providers.registry import ProviderRegistry

    g = db.get(AgentGoal, goal_id)
    if not g:
        raise ValueError("目标不存在")
    if g.status == "done":
        return g
    from app.services.agent.engine.sessions import list_messages

    msgs = list_messages(db, g.session_id)[-_MAX_HISTORY:]
    transcript = "\n".join(
        f"{'用户' if m.role == 'user' else '助手'}: {(m.content or '')[:300]}"
        for m in msgs if m.role in ("user", "assistant")
    )[:4000]
    model = _resolve_chat_model(db, model_id)
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是目标达成评估器。判断创作目标是否已达成。\n"
                    "输出严格 JSON："
                    '{"reached": true/false, "progress": "当前进度摘要(≤80字，描述已完成的产出)"}\n'
                    "只有完成标准全部满足才算 reached=true。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"【目标】{g.goal}\n【完成标准】{g.criteria or '（未指定）'}\n"
                    f"【对话进展】\n{transcript}"
                ),
            },
        ]
    )
    raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _parse_json_flexible(raw)
    if data.get("reached"):
        g.status = "done"
        g.progress_summary = (data.get("progress") or "目标已达成").strip()
    else:
        g.step_count += 1
        if data.get("progress"):
            g.progress_summary = str(data["progress"]).strip()
    db.commit()
    db.refresh(g)
    return g


def goal_advance_message(g) -> str:
    """构造 Goal 推进用的用户消息：目标 + 当前进度 + 推进指令。"""
    progress = g.progress_summary or "尚未开始"
    return (
        f"【创作目标推进】\n目标：{g.goal}\n完成标准：{g.criteria or '（未指定）'}\n"
        f"当前进度：{progress}\n\n"
        "请基于以上目标继续推进一步：完成该阶段最关键的产出，并简要汇报这一步完成了什么、"
        "下一步打算做什么。"
    )
