"""引擎内核层 · 上下文构建：system prompt / 历史→messages / @引用 / 摘要 / 标题。

从 agent_service.py 剥离（原行号 1156~1836 区域），逻辑未改动。
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentMessage, AgentSession
from app.models.model_config import Model
from app.services.agent.engine.constants import (
    _CTX_REF_LIMITS,
    _MAX_HISTORY,
    _SUMMARY_THRESHOLD,
    _SYSTEM_PROMPT,
)
from app.services.agent.memory.store import _list_memories, _relevant_memories
from app.services.agent.tools.meta import _load_enabled_rules, _load_role_configs

logger = logging.getLogger(__name__)


def _resolve_context_refs(db: Session, refs) -> str:
    """把 @ 引用的上下文（项目/剧本文档/历史会话/资产）解析为可注入文本。

    返回一段「【引用上下文】…」文本；无法解析的资源静默跳过。
    """
    if not refs:
        return ""
    parts: list[str] = []
    for ref in refs:
        rid = str(ref.id)
        try:
            if ref.type == "project":
                from app.models.project import Project
                p = db.get(Project, rid)
                if not p:
                    continue
                from app.models.asset import Asset
                assets = db.scalars(
                    select(Asset).where(Asset.project_id == p.id).limit(12)
                ).all()
                asset_line = "；".join(f"{a.name}({a.type})" for a in assets) or "（暂无资产）"
                text = (
                    f"项目「{p.title}」\n"
                    f"- 画幅：{p.aspect_ratio}｜风格：{p.art_style_prompt or '未设置'}\n"
                    f"- 梗概：{(p.synopsis or '无')[:400]}\n"
                    f"- 创作规则：{(p.rules or '未设置')[:500]}\n"
                    f"- 已有资产：{asset_line}\n"
                    f"- 剧本：{(p.script or '未编写')[:1200]}"
                )
            elif ref.type == "document":
                from app.models.novel import Novel
                n = db.get(Novel, rid)
                if not n:
                    continue
                analysis = n.analysis_result if isinstance(n.analysis_result, dict) else {}
                # 分析结果顶层无 summary：取 outline，否则取各章摘要拼接
                outline = (analysis.get("outline") or "").strip()
                if not outline:
                    chap = analysis.get("chapters_summary") or []
                    if chap:
                        outline = "；".join(
                            str(c.get("summary") or "")[:80] for c in chap[:3]
                        )
                text = (
                    f"小说《{n.title}》（{n.word_count} 字，{n.chapters_count} 章）\n"
                    f"- 分析：{(outline[:300] if outline else '未分析')}\n"
                    f"- 正文开头：{(n.raw_text or '')[:2400]}"
                )
            elif ref.type == "session":
                from app.services.agent.engine.sessions import list_messages
                s = db.get(AgentSession, rid)
                if not s:
                    continue
                msgs = list_messages(db, s.id)[-6:]
                lines = [
                    f"{'用户' if m.role == 'user' else '助手' if m.role == 'assistant' else '工具'}: {(m.content or '')[:200]}"
                    for m in msgs
                ]
                text = f"历史会话「{s.title}」\n" + "\n".join(lines)
            elif ref.type == "asset":
                from app.models.asset import Asset
                a = db.get(Asset, rid)
                if not a:
                    continue
                text = f"资产「{a.name}」（{a.type}）：{(a.description or a.expanded_description or '无描述')[:400]}"
            else:
                continue
            parts.append(text[:_CTX_REF_LIMITS.get(ref.type, 1000)])
        except Exception:
            logger.warning("解析上下文引用失败: %s/%s", ref.type, rid, exc_info=True)
    if not parts:
        return ""
    return "【引用上下文】\n" + "\n\n".join(parts)


def _build_system_prompt(
    db: Session, summary: str | None = None, role: str | None = None, project_id=None,
    memory_query: str = "",
) -> str:
    """构建 system prompt：角色人设（可选）+ 基础人设 + 历史摘要 + 长期记忆 + Skill 指南。

    P9 记忆升级：传入 memory_query（最近用户消息）时按语义召回最相关记忆注入，
    否则回退全量注入（兼容无 query 的调用方）。
    """
    from app.models.agent import AgentSkill

    role_cfg = _load_role_configs(db).get(role or "")
    if role_cfg:
        prompt = (
            f"{role_cfg['prompt']}\n\n"
            "你仍是「创作助手」平台的一部分，可以调用系统工具完成任务，"
            "回答使用简体中文，保持专业、友好、简洁。\n\n"
            "【工具使用规则】\n"
            "1. 当用户请求涉及工具能力时，调用对应工具。\n"
            "2. 每个用户请求内，每个工具最多调用一次；拿到工具执行结果后，"
            "必须立即用简洁中文给出最终回答，禁止重复调用同一工具。\n"
            "3. 工具执行失败时，如实告知用户，并给出替代建议。"
        )
    else:
        prompt = _SYSTEM_PROMPT
    if summary:
        prompt += "\n\n【历史对话摘要】\n" + summary
    if memory_query:
        # P9 语义召回：按当前消息召回最相关记忆（替换全量注入）
        memories = _relevant_memories(db, memory_query, project_id=project_id)
    else:
        memories = _list_memories(db, project_id=project_id)
    if memories:
        prompt += "\n\n【长期记忆】\n" + "\n".join(f"- {m}" for m in memories)
    # 规则注入（对齐 TraeWork Rules）：全局规则 + 当前项目项目级规则
    rules = _load_enabled_rules(db, project_id=project_id)
    if rules:
        prompt += "\n\n【规则（必须遵守）】\n" + "\n".join(rules)
    # 渐进式披露（对齐 hermes/OpenClaw）：不再全量注入 knowledge 型 Skill 正文
    has_knowledge_skills = db.scalar(
        select(AgentSkill.id)
        .where(AgentSkill.enabled.is_(True), AgentSkill.tool_type == "knowledge")
        .limit(1)
    )
    if has_knowledge_skills:
        prompt += (
            "\n\n【技能库】平台预置了若干专业 Skill（领域知识/工作流规范/参考模板）。"
            "当用户需求可能涉及专业能力、或你不确定该按什么流程处理时："
            "先调用 skill_list 查看可用技能清单，再调用 skill_view 加载所需技能的完整内容，"
            "并严格按其说明执行。执行完技能后基于结果回答用户。"
        )
    return prompt


def _digest_attachments(attachments: list[dict] | None) -> tuple[list[str], list[str]]:
    """P9 多模态附件消化（当次注入与历史回放共用）：
    图片/视频帧 → 视觉图片列表；音频转写/文档文本 → 消息文本片段。
    """
    images: list[str] = []
    text_parts: list[str] = []
    for att in (attachments or []):
        kind = att.get("kind")
        name = att.get("name") or "附件"
        if kind == "image" and att.get("url"):
            images.append(att["url"])
        elif kind == "video":
            for f in (att.get("frames") or []):
                if f:
                    images.append(f)
            if att.get("url"):
                images.append(att["url"])  # 原视频也可作视觉/参考素材
        elif kind == "audio" and att.get("transcript"):
            text_parts.append(f"【用户上传的音频附件「{name}」转写】\n{att['transcript']}")
        elif kind == "document" and att.get("text"):
            text_parts.append(f"【用户上传的文档附件「{name}」内容】\n{att['text']}")
    return images, text_parts


def _to_chat_messages(history: list[AgentMessage]) -> list[dict]:
    """历史消息 → OpenAI 格式。tool 消息展开为可读文本；user 消息携带图片时用 content 数组（视觉理解）。"""
    out: list[dict] = []
    for m in history:
        if m.role == "user":
            content = m.content or ""
            images = [u for u in (m.images or []) if u]
            # P9 附件持久化回放：图片/视频帧重新进视觉；音频转写/文档文本拼回消息
            att_images, att_texts = _digest_attachments(m.attachments)
            if att_texts:
                content = "\n\n".join([content] + att_texts) if content else "\n\n".join(att_texts)
            all_images = [*images, *att_images]
            if all_images:
                parts: list[dict] = []
                if content:
                    parts.append({"type": "text", "text": content})
                for img in all_images:
                    parts.append({"type": "image_url", "image_url": {"url": img}})
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": "user", "content": content})
        elif m.role == "assistant":
            # 流式增量落库的占位消息（生成中/断连残留）：不回放进上下文，避免空回复污染
            if not m.completed:
                continue
            out.append({"role": "assistant", "content": m.content or ""})
        elif m.role == "tool":
            text = m.content or ""
            if text:
                out.append({
                    "role": "user",
                    "content": f"[工具 {m.tool_name} 执行结果（系统自动回填，非用户消息）]\n{text}",
                })
    return out[-_MAX_HISTORY:]


def _summarize_history(db: Session, msgs: list[AgentMessage], model: Model) -> str:
    """把早期对话压缩成中文摘要（≤300 字），供上下文裁剪注入 system prompt。"""
    from app.providers.registry import ProviderRegistry

    lines = []
    for m in msgs:
        role = "用户" if m.role == "user" else ("助手" if m.role == "assistant" else f"工具({m.tool_name})")
        lines.append(f"{role}: {m.content or ''}")
    text = "\n".join(lines)[:6000]
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是对话摘要器。把用户与 AI 助手的早期对话压缩成简体中文摘要，"
                    "保留关键事实、用户偏好、已完成的操作与结果、待办事项。"
                    "控制在 300 字以内，用简洁条目或短段落。只输出摘要本身，不要任何说明。"
                ),
            },
            {"role": "user", "content": text},
        ]
    )
    summary = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return summary.strip()


def _build_context_messages(
    db: Session, session: AgentSession, history: list[AgentMessage], model: Model,
    role: str | None = None, context_text: str = "", project_id=None,
) -> list[dict]:
    """构建模型上下文：历史超过阈值时自动压缩——生成/刷新早期摘要（缓存到 session.summary），只传最近消息。

    覆盖式刷新：每次超阈值都以当前完整早期历史重新生成摘要，避免已存在摘要后
    中间段历史被静默丢弃、摘要长期不更新的问题（T4 自动上下文压缩）。
    """
    if len(history) > _SUMMARY_THRESHOLD:
        try:
            summary = _summarize_history(db, history[:-_MAX_HISTORY], model)
            if summary:
                session.summary = summary  # 覆盖式刷新：摘要始终反映完整早期历史，不重复累积
                db.commit()
        except Exception:
            logger.exception("生成对话历史摘要失败")
            db.rollback()
    # P9 记忆语义召回：用最近一条用户消息作为检索 query（无用户消息时全量注入）
    memory_query = ""
    for m in reversed(history):
        if m.role == "user":
            memory_query = (m.content or "").strip()
            break
    system = _build_system_prompt(
        db, session.summary, role, project_id=project_id, memory_query=memory_query,
    )
    if context_text:
        system += "\n\n" + context_text
    messages = [{"role": "system", "content": system}]
    messages += _to_chat_messages(history)
    return messages


def _ctx_tokens(history: list[AgentMessage], system_text: str = "") -> int:
    """粗略估算上下文 token 用量（消息内容/2 + 图片 500/张 + system prompt），供前端展示使用率。"""
    total = len(system_text or "")
    for m in history:
        total += len(m.content or "") + (len(m.images or []) * 500)
    return max(1, total // 2)


def _auto_generate_title(db: Session, session: AgentSession, model: Model) -> None:
    """用 LLM 自动生成会话标题（≤20 字）；失败时静默回退为首条消息截断。"""
    from app.providers.registry import ProviderRegistry

    if session.title and session.title != "新对话":
        return
    first = db.scalar(
        select(AgentMessage.content)
        .where(AgentMessage.session_id == session.id, AgentMessage.role == "user")
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        .limit(1)
    )
    if not first:
        return
    try:
        provider = ProviderRegistry.for_model(model)
        resp = provider.chat(
            [
                {
                    "role": "system",
                    "content": "你是对话标题生成器。根据用户第一句话，用简体中文生成一个不超过 15 字的对话标题，直接输出标题，不要引号、不要解释。",
                },
                {"role": "user", "content": first},
            ]
        )
        title = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        # 清洗：模型偶发输出 markdown 标题/列表符、引号与换行，只保留纯文本
        title = re.sub(r"^[#>*\-]*\s*", "", title.strip().strip('"“”‘’'))
        title = re.sub(r"\s+", " ", title.replace("\n", " ")).strip(" #")
        if title:
            session.title = title[:20]
            db.commit()
            return
    except Exception as e:
        logger.warning("LLM 自动生成会话标题失败，回退截断: %s", e)
    fallback = first.strip().replace("\n", " ")[:20]
    if fallback:
        session.title = fallback
        db.commit()
