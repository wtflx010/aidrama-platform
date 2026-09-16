"""记忆层 · 记忆 CRUD / 指令处理 / 自动记忆 / 创作决策记忆。

从 agent_service.py 剥离（原行号 1240~1614 区域），逻辑未改动：
- _relevant_memories / _list_memories：对话注入用
- _has_memory_instruction / _handle_memory：显式「记住/忘记」指令
- list_memory_items / create_memory_item / delete_memory_item：管理界面
- auto_remember / _spawn_auto_remember：AI 偏好自动记忆
- creative_decision_memory / _spawn_creative_decision_memory：创作决策沉淀
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.database import SessionLocal
from app.models.agent import AgentMemory
from app.models.model_config import Model
from app.services.agent.engine.models import _resolve_chat_model
from app.services.agent.engine.parsing import _parse_json_flexible

logger = logging.getLogger(__name__)

# 后台 AI 自动记忆的共享有界线程池（避免每轮对话无界起线程，耗尽 DB 连接池）
_AUTO_REMEMBER_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-remember")


def _relevant_memories(db, query: str, project_id=None, top_n: int = 5) -> list[str]:
    """对话注入用：按当前用户消息语义召回最相关的记忆（替换全量注入，精准且省上下文）。"""
    from app.services.agent.memory.tfidf import search_memories

    hits = search_memories(db, query, project_id=project_id, top_n=top_n)
    return [h["content"] for h in hits]


def _list_memories(db, project_id=None) -> list[str]:
    """返回长期记忆（全局 + 指定项目级；按创建时间升序）。"""
    q = select(AgentMemory).order_by(AgentMemory.created_at.asc())
    items = list(db.scalars(q).all())
    out = []
    for m in items:
        if m.scope == "project":
            if project_id and str(m.project_id) == str(project_id):
                out.append(m.content)
        else:
            out.append(m.content)
    return out


def _has_memory_instruction(text: str) -> bool:
    """用户消息是否含显式记忆指令（记住/忘记/删除记忆）。"""
    return any(k in (text or "") for k in ("记住", "请记住", "帮我记住", "忘记", "忘掉", "删除记忆"))


def _handle_memory(db, message: str, project_id=None) -> None:
    """识别并处理记忆指令：`记住…` 保存 / `忘记…` 删除（跨会话长期记忆）。"""
    text = (message or "").strip()
    if not text:
        return
    # 忘记：删除内容含关键词的记忆（优先匹配「风格/设定/规则…」等偏好名词，其次短语）
    if "忘记" in text or "删除记忆" in text or "忘掉" in text:
        kws = re.findall(
            r"(?:忘记|忘掉)\s*(?:关于|掉)?\s*([\u4e00-\u9fa5A-Za-z0-9]{1,20}?"
            r"(?:风格|设定|规则|偏好|名字|姓名|角色|题材|要求|习惯|记忆))",
            text,
        )
        if not kws:
            kws = re.findall(
                r"(?:忘记|忘掉)\s*(?:关于|掉)?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,6})", text,
            )
        for kw in kws:
            for m in db.scalars(select(AgentMemory)).all():
                if kw in m.content:
                    db.delete(m)
        if "删除记忆" in text:
            for m in db.scalars(select(AgentMemory)).all():
                db.delete(m)
        db.commit()
        return
    # 记住：提取「记住/请记住/帮我记住」之后的内容
    for m in re.finditer(r"(?:请记住|帮我记住|记住)\s*[:：,，]?\s*(.+)", text):
        content = m.group(1).strip().strip("。！？!?~～")
        if 2 <= len(content) <= 200:
            scope = "project" if project_id else "global"
            db.add(AgentMemory(scope=scope, content=content, project_id=project_id))
            db.commit()
            return


def list_memory_items(db, scope: str | None = None, project_id=None) -> list:
    """记忆管理：返回记忆列表（含 id/scope/content），供前端查看与删除。"""
    q = select(AgentMemory).order_by(AgentMemory.created_at.desc())
    if scope in ("global", "project"):
        q = q.where(AgentMemory.scope == scope)
    if project_id:
        q = q.where(AgentMemory.project_id == project_id)
    return list(db.scalars(q).all())


def create_memory_item(db, content: str, scope: str = "global", project_id=None):
    """手动添加记忆（记忆管理页）。"""
    content = (content or "").strip()
    if not (2 <= len(content) <= 300):
        raise ValueError("记忆内容需 2~300 字")
    if scope not in ("global", "project"):
        raise ValueError("记忆范围只能为 global / project")
    if scope == "project" and not project_id:
        raise ValueError("项目级记忆必须指定项目")
    m = AgentMemory(scope=scope, content=content, project_id=project_id)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def delete_memory_item(db, memory_id) -> bool:
    m = db.get(AgentMemory, memory_id)
    if not m:
        return False
    db.delete(m)
    db.commit()
    return True


def auto_remember(db, user_message: str, model: Model, project_id=None) -> None:
    """AI 自动记忆：对话后由 LLM 判断用户消息中是否有值得长期记住的偏好/规则，有则入库。

    只在用户消息含明显偏好信号（喜欢/偏好/以后/记住风格的用词）时才调用，避免噪音。
    应由后台线程调用（_spawn_auto_remember），避免阻塞对话首响应。
    """
    if not user_message or len(user_message) < 6:
        return
    # 快速预筛：无偏好信号不触发 LLM（省一次调用）
    signals = ("我喜欢", "我偏好", "偏好", "以后", "风格要", "记得要", "希望都", "习惯")
    if not any(s in user_message for s in signals):
        return
    try:
        from app.providers.registry import ProviderRegistry
        provider = ProviderRegistry.for_model(model)
        resp = provider.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "判断下面这条用户消息是否包含值得跨对话长期记住的创作偏好或规则"
                        "（如美术风格、写作习惯、角色设定、命名规则）。"
                        '只输出 JSON：{"should_save": true/false, "memory": "要记住的内容"}\n'
                        "memory 用一句话概括（≤60字）；没有值得记住的内容时 should_save=false。"
                    ),
                },
                {"role": "user", "content": user_message[:500]},
            ]
        )
        raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        data = _parse_json_flexible(raw)
        if data.get("should_save") and data.get("memory"):
            content = str(data["memory"]).strip().strip("。！？")
            if 2 <= len(content) <= 200:
                scope = "project" if project_id else "global"
                # 去重：同一范围已存在相同内容则跳过，避免偏好反复入库
                dup = db.scalar(
                    select(AgentMemory).where(
                        AgentMemory.scope == scope,
                        AgentMemory.content == content,
                    )
                )
                if dup:
                    return
                db.add(AgentMemory(scope=scope, content=content, project_id=project_id))
                db.commit()
                logger.info("AI 自动记忆已保存（%s）: %s", scope, content[:40])
    except Exception:
        logger.warning("AI 自动记忆失败，跳过", exc_info=True)


def _spawn_auto_remember(user_message: str, model_id, project_id=None) -> None:
    """后台线程执行 AI 自动记忆（不阻塞对话首响应）。

    线程内新建独立 db session + 重新解析模型，避免跨线程共享 ORM session。
    复用模块级有界线程池（max_workers=2），高并发下排队而非无限起线程。
    """

    def _run() -> None:
        try:
            db = SessionLocal()
            try:
                model = _resolve_chat_model(db, model_id)
                auto_remember(db, user_message, model, project_id=project_id)
            finally:
                db.close()
        except Exception:
            logger.warning("后台自动记忆线程异常", exc_info=True)

    try:
        _AUTO_REMEMBER_POOL.submit(_run)
    except Exception:  # noqa: BLE001 线程池满/关闭等极端情况：静默丢弃，不影响对话
        logger.warning("后台自动记忆提交失败，跳过", exc_info=True)


# P3 创作决策记忆：预筛信号（命中才触发 LLM，由 LLM 严格判定是否入库）
_CREATIVE_DECISION_SIGNALS = (
    "不要", "别用", "不喜欢", "不行", "不太好", "不好", "重写", "重新写",
    "改成", "换成", "改用", "调整", "修改", "改一", "换",
    "风格", "题材", "主角", "角色", "设定", "色调", "画风", "开头", "结尾",
    "场景", "名字", "就叫", "就按", "就用", "定了", "可以了", "没问题",
)
# 咨询类标记：纯提问不沉淀创作决策（避免「分析剧情」「这版怎么样」误触发）
_CONSULTATION_MARKERS = ("分析", "解释", "怎么看", "如何", "怎么样", "为什么", "对比", "区别")
# 强决策词：即使消息带咨询标记，命中这些仍视为创作决策
_STRONG_DECISION_WORDS = (
    "不要", "别用", "不喜欢", "不行", "重写", "重新写", "改成", "换成", "就叫", "就按", "就用", "定了",
)


def creative_decision_memory(
    db, user_message: str, assistant_reply: str, model: Model, project_id=None,
) -> None:
    """P3 创作决策记忆沉淀：从一轮「用户消息 + 助手回复」中提炼值得跨对话记住的创作决策。

    与 auto_remember（只认「喜欢/偏好/以后」等显式偏好陈述）互补：本函数聚焦交互过程中的
    创作决策——用户否决了某设定/版本/风格、确定了题材/风格/主角名、修正了创作规则。
    预筛命中才调 LLM，由 LLM 严格判定 should_save，避免临时性修改等噪音入库。
    """
    text = (user_message or "").strip()
    if len(text) < 6:
        return
    if not any(s in text for s in _CREATIVE_DECISION_SIGNALS):
        return
    # 纯咨询类消息（问句/分析请求）跳过；带强决策词（如「不要」「改成」）的不跳过
    if any(m in text for m in _CONSULTATION_MARKERS) and not any(
        s in text for s in _STRONG_DECISION_WORDS
    ):
        return
    try:
        from app.providers.registry import ProviderRegistry
        provider = ProviderRegistry.for_model(model)
        resp = provider.chat([
            {
                "role": "system",
                "content": (
                    "你是创作助手的记忆提炼器。根据用户本轮消息与助手回复，判断是否有值得"
                    "跨对话长期记住的创作决策或偏好，例如：用户否决了某设定/版本/风格；"
                    "确定了题材/风格/主角名；修正了创作规则（如「对白再短一点」）。\n"
                    '只输出 JSON：{"should_save": true/false, "memory": "一句话概括（≤60字，从用户角度）"}\n'
                    "临时性、一次性的调整（如「第二段改一下」「加一个镜头」）没有长期价值，should_save=false。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"【用户消息】\n{text[:500]}\n\n"
                    f"【助手回复】\n{(assistant_reply or '')[:800]}"
                ),
            },
        ])
        raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        data = _parse_json_flexible(raw)
        if data.get("should_save") and data.get("memory"):
            content = str(data["memory"]).strip().strip("。！？")
            if 2 <= len(content) <= 200:
                scope = "project" if project_id else "global"
                # 去重：同范围已存在相同内容则跳过，避免反复入库
                dup = db.scalar(
                    select(AgentMemory).where(
                        AgentMemory.scope == scope,
                        AgentMemory.content == content,
                    )
                )
                if dup:
                    return
                db.add(AgentMemory(scope=scope, content=content, project_id=project_id))
                db.commit()
                logger.info("创作决策记忆已保存（%s）: %s", scope, content[:40])
    except Exception:
        logger.warning("创作决策记忆失败，跳过", exc_info=True)


def _spawn_creative_decision_memory(
    user_message: str, assistant_reply: str, model_id, project_id=None,
) -> None:
    """后台线程执行创作决策记忆（不阻塞对话流；复用自动记忆线程池）。"""

    def _run() -> None:
        try:
            db = SessionLocal()
            try:
                model = _resolve_chat_model(db, model_id)
                creative_decision_memory(
                    db, user_message, assistant_reply, model, project_id=project_id,
                )
            finally:
                db.close()
        except Exception:
            logger.warning("后台创作决策记忆线程异常", exc_info=True)

    try:
        _AUTO_REMEMBER_POOL.submit(_run)
    except Exception:  # noqa: BLE001 线程池满/关闭等极端情况：静默丢弃，不影响对话
        logger.warning("后台创作决策记忆提交失败，跳过", exc_info=True)
