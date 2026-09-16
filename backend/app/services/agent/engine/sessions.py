"""引擎内核层 · 会话管理：会话 CRUD / 搜索 / fork / compact / 消息列表。

从 agent_service.py 剥离（原行号 820~980 区域），逻辑未改动。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentMessage, AgentSession
from app.services.agent.engine.constants import _MAX_HISTORY
from app.services.agent.engine.models import _resolve_chat_model

logger = logging.getLogger(__name__)


def list_sessions(db: Session) -> list[AgentSession]:
    return list(db.scalars(select(AgentSession).order_by(AgentSession.updated_at.desc())).all())


def search_sessions(db: Session, q: str, limit: int = 20) -> list[dict]:
    """跨会话搜索历史消息（P8 Phase 3）：LIKE 匹配 user/assistant 消息正文。

    返回按时间倒序的命中列表，每条含所属会话、消息摘要（命中位置上下文）、
    消息 id（供前端跳转定位）。数据量小先用 LIKE，量大再迁移 SQLite FTS5。
    """
    kw = (q or "").strip()
    if not kw:
        return []
    kw_lower = kw.lower()
    rows = db.execute(
        select(AgentMessage, AgentSession.title)
        .join(AgentSession, AgentSession.id == AgentMessage.session_id)
        .where(
            AgentMessage.role.in_(["user", "assistant"]),
            AgentMessage.content.is_not(None),
            AgentMessage.content.ilike(f"%{kw}%"),
        )
        .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
        .limit(min(max(int(limit), 1), 50))
    ).all()
    hits: list[dict] = []
    for m, title in rows:
        content = m.content or ""
        idx = content.lower().find(kw_lower)
        start = max(0, idx - 40) if idx >= 0 else 0
        snippet = content[start : start + 160].replace("\n", " ")
        if start > 0:
            snippet = "…" + snippet
        if start + 160 < len(content):
            snippet += "…"
        hits.append({
            "session_id": str(m.session_id),
            "session_title": title,
            "message_id": str(m.id),
            "role": m.role,
            "snippet": snippet,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return hits


def create_session(db: Session, title: str | None = None) -> AgentSession:
    s = AgentSession(title=(title or "").strip() or "新对话")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def delete_session(db: Session, session_id) -> bool:
    s = db.get(AgentSession, session_id)
    if not s:
        return False
    db.delete(s)
    db.commit()
    return True


def fork_session(db: Session, session_id, after_message_id: str | None = None) -> AgentSession:
    """创建会话副本（Fork Chat）：复制到指定消息之前的所有消息到新会话，继承标题。"""
    src = db.get(AgentSession, session_id)
    if not src:
        raise ValueError("会话不存在")
    new = AgentSession(title=src.title + "（副本）", summary=src.summary)
    db.add(new)
    db.flush()
    for m in list_messages(db, session_id):
        if after_message_id and str(m.id) == after_message_id:
            break
        # created_at 显式复制原时间戳：同一事务内 server_default now() 会使全部相同，导致按时间排序不稳
        db.add(AgentMessage(
            session_id=new.id, role=m.role, content=m.content, images=m.images,
            tool_name=m.tool_name, tool_params=m.tool_params, tool_status=m.tool_status,
            media_urls=m.media_urls, project_id=m.project_id, created_at=m.created_at,
        ))
    db.commit()
    db.refresh(new)
    return new


def optimize_prompt(db: Session, message: str, model_id=None) -> str:
    """提示词优化：把口语化描述改写为结构化、可直接用于 AI 生成的指令。"""
    from app.providers.registry import ProviderRegistry

    model = _resolve_chat_model(db, model_id)
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是提示词优化专家。把用户的口语化描述改写为清晰、结构化、"
                    "可直接用于 AI 生图/生视频/创作的指令。保留用户全部意图，"
                    "补充必要细节（主体/动作/环境/光线/风格/比例等）。"
                    "只输出优化后的指令，不要解释、不要加引号。"
                ),
            },
            {"role": "user", "content": message},
        ]
    )
    out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return out.strip() or message


def compact_session(db: Session, session_id, model_id=None) -> dict:
    """手动压缩上下文：把最旧部分生成摘要，删除冗余消息，保留最近 _MAX_HISTORY 条。"""
    from app.services.agent.engine.context import _summarize_history

    session = db.get(AgentSession, session_id)
    if not session:
        raise ValueError("会话不存在")
    msgs = list_messages(db, session_id)
    if len(msgs) <= _MAX_HISTORY:
        return {"compacted": False, "message": "上下文不长，无需压缩"}
    model = _resolve_chat_model(db, model_id)
    summary = _summarize_history(db, msgs[:-_MAX_HISTORY], model)
    if not summary:
        raise ValueError("压缩失败，请稍后重试")
    keep_ids = [str(m.id) for m in msgs[-_MAX_HISTORY:]]
    db.query(AgentMessage).filter(
        AgentMessage.session_id == session.id,
        AgentMessage.id.notin_(keep_ids),
    ).delete(synchronize_session=False)
    session.summary = summary
    db.commit()
    return {"compacted": True, "message": "已压缩，早期对话已归档为摘要", "summary": summary[:120]}


def list_messages(db: Session, session_id) -> list[AgentMessage]:
    # 按时间正序；加 id 作 tiebreaker（SQLite created_at 精度到秒，同秒多条时保证稳定顺序）
    msgs = list(
        db.scalars(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        ).all()
    )
    # 2026-08-16 兜底清理：生成中断（后端进程被杀/SSE 断连）会残留 completed=false 的
    # 占位消息，前端会因此永远显示「思考中/继续生成中」图标（轮询重置了本地 6 分钟
    # 兜底计时器）。这里按「最后更新时间 > 10 分钟仍未完成」惰性标记完成：
    # 保留已生成内容（thinking/正文），仅结束生成中状态，前端弧度正常收起。
    import time as _t

    cutoff = _t.time() - 600
    healed = False
    for m in msgs:
        if m.role == "assistant" and m.completed is False:
            updated = m.updated_at.timestamp() if m.updated_at else 0
            if updated < cutoff:
                m.completed = True
                healed = True
    if healed:
        db.commit()
    return msgs
