"""创作对接层 · 创作角色子智能体：独立会话 / 并行执行 / 创作状态卡。

从 agent_service.py 剥离（原行号 3240~3523 + 3698~3697 的 _collect_script_context），
逻辑未改动。本模块只做「专业角色 LLM 调用 + 独立会话持久化 + 状态卡」；
工具级包装（subagent_* 工具）位于 tools/executor。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.providers.registry import ProviderRegistry
from app.services.agent.engine.constants import _CHAT_TOTAL_TIMEOUT, _SUBAGENT_HISTORY_TURNS
from app.services.agent.engine.models import _resolve_chat_model
from app.services.agent.tools.meta import _load_role_configs

logger = logging.getLogger(__name__)


def _load_subagent_history(db: Session, session_id, role_key: str) -> list[dict]:
    """加载子智能体独立会话历史：最近 _SUBAGENT_HISTORY_TURNS 轮「任务→产出」（早→晚）。

    仅返回该 (主会话, 子智能体) 自己的历史，不混入主对话内容（独立上下文）。
    """
    if not session_id:
        return []
    from app.models.agent import AgentSubagentMessage

    msgs = db.scalars(
        select(AgentSubagentMessage)
        .where(
            AgentSubagentMessage.session_id == session_id,
            AgentSubagentMessage.role_key == role_key,
        )
        .order_by(AgentSubagentMessage.created_at.desc(), AgentSubagentMessage.id.desc())
        .limit(_SUBAGENT_HISTORY_TURNS * 2)
    ).all()
    return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


def _save_subagent_turn(db: Session, session_id, role_key: str, task: str, output: str) -> None:
    """持久化一轮子智能体独立会话（任务→产出；仅成功产出时记录，失败不污染历史）。"""
    if not session_id or not task or not output:
        return
    from app.models.agent import AgentSubagentMessage

    db.add_all([
        AgentSubagentMessage(session_id=session_id, role_key=role_key, role="user", content=task),
        AgentSubagentMessage(session_id=session_id, role_key=role_key, role="assistant", content=output),
    ])
    # P1 状态层：同步更新创作状态卡（该角色产出即「当前版本」），与上面同一事务提交
    _upsert_creative_state(db, session_id, role_key, output)
    db.commit()


def _upsert_creative_state(db: Session, session_id, role_key: str, content: str) -> None:
    """更新创作状态卡：记录该角色最新产出为「当前版本」（version 自增，覆盖旧快照）。

    状态卡是会话创作内容的单一事实来源：创建项目/分镜时优先读取，
    避免从聊天历史中推断哪个版本最新而拿错旧版本。不单独 commit（由调用方统一提交）。
    """
    from app.models.agent import AgentCreativeState

    if not session_id or not content:
        return
    row = db.scalar(
        select(AgentCreativeState).where(
            AgentCreativeState.session_id == session_id,
            AgentCreativeState.role_key == role_key,
        )
    )
    if row:
        row.version += 1
        row.content = content
    else:
        db.add(AgentCreativeState(
            session_id=session_id, role_key=role_key, version=1, content=content,
        ))


def _maybe_track_main_creative(db: Session, session_id, user_text: str, content: str) -> None:
    """P2 主对话剧本追踪：把主对话直接产出的剧本写入创作状态卡（role_key="main"）。

    启发式判定（仅尽力记录，不阻塞对话）：
    - 回复足够长（≥200 字，剧本通常远超此量）
    - 用户本轮消息命中创作意图（动词 + 创作名词，如「写个剧本」「重写这个故事」）
    - 本轮若已由编剧子智能体产出剧本、最终回复只是复述原文，跳过以免重复记录
    判定失败（普通闲聊/咨询）则不写入，创建项目时该槽位回退到聊天历史收集。
    """
    from app.services.agent.engine.constants import _has_main_creative_intent

    if len(content or "") < 200:
        return
    if not _has_main_creative_intent(user_text or ""):
        return
    from app.models.agent import AgentCreativeState

    sc = db.scalar(
        select(AgentCreativeState).where(
            AgentCreativeState.session_id == session_id,
            AgentCreativeState.role_key == "screenwriter",
        )
    )
    if sc and sc.content and (sc.content in content or content in sc.content):
        return  # 最终回复只是复述编剧子智能体剧本，不重复记录
    _upsert_creative_state(db, session_id, "main", content)
    db.commit()


def _collect_creative_state_context(db: Session, session_id) -> str | None:
    """从创作状态卡读取各角色「当前版本」（单一事实来源，P1 状态层）。

    有状态卡数据时优先使用（创建项目不再从聊天历史推断哪个版本最新）；
    无数据返回 None，由调用方回退到历史消息收集（_collect_script_context）。
    剧本槽位（编剧 + 主对话产出）取更新时间最新者为「当前剧本」，其次导演/美术。
    """
    from app.models.agent import AgentCreativeState

    rows = db.scalars(
        select(AgentCreativeState).where(AgentCreativeState.session_id == session_id)
    ).all()
    if not rows:
        return None
    role_labels = {"screenwriter": "编剧", "director": "导演", "artist": "美术", "main": "主对话"}
    order = {"screenwriter": 0, "main": 1, "director": 2, "artist": 3}
    parts: list[str] = []
    # 剧本槽位：编剧/主对话都可能产出剧本，以更新时间最新者为准，避免新旧版本冲突
    script_rows = [r for r in rows if r.role_key in ("screenwriter", "main")]
    script_row = max(script_rows, key=lambda r: r.updated_at or r.created_at) if script_rows else None
    if script_row:
        label = role_labels.get(script_row.role_key, script_row.role_key)
        # 剧本槽位放宽到 10000 字：剧本通常较长，截断太紧会导致
        # 生成项目时只保留开头部分剧情（幕数/集数被压缩）
        parts.append(f"【{label}·当前版本 v{script_row.version}，以此为准】\n{(script_row.content or '')[:30000]}")
    for r in sorted((x for x in rows if x.role_key not in ("screenwriter", "main")),
                    key=lambda x: order.get(x.role_key, 99)):
        label = role_labels.get(r.role_key, r.role_key)
        parts.append(f"【{label}·当前版本 v{r.version}，以此为准】\n{(r.content or '')[:20000]}")
    return "\n\n".join(parts)[:60000]


def _run_subagent(db: Session, session_id, role_key: str, task: str, refs: list[str] | None = None, model=None) -> str:
    """执行子智能体（Subagent）：独立上下文窗口的专业角色 LLM 调用，返回产出文本。

    - 独立 system prompt（角色人设 + 独立完成任务的要求）
    - 独立会话：注入该子智能体最近几轮「任务→产出」历史（按主会话隔离），跨调用保持连续
    - 只传本子任务，不注入主对话历史（真正的独立上下文）
    - 视觉类角色（导演/美术）在提供参考图时把图一并传入（视觉理解）
    输出非流式，最长约 1200 字。
    """
    cfg = _load_role_configs(db).get(role_key)
    if not cfg:
        raise ValueError(f"未知子智能体：{role_key}")
    if model is None:
        model = _resolve_chat_model(db, None)
    history = _load_subagent_history(db, session_id, role_key)
    out = _run_subagent_core(cfg, task, refs=refs, model=model, history=history)
    _save_subagent_turn(db, session_id, role_key, task, out)
    return out


def _run_subagent_core(
    cfg: dict, task: str, refs: list[str] | None = None, model=None,
    history: list[dict] | None = None, provider=None,
) -> str:
    """子智能体纯 LLM 调用（无 DB 访问，供并行线程安全调用）。

    history：该子智能体独立会话的历史消息（[{role, content}]，早→晚），
    以真实 user/assistant 消息注入上下文；无历史时保持原有纯独立调用。
    provider：预创建的 provider 实例（并行路径在主线程创建后传入，避免 worker
    线程触碰 ORM model 触发懒加载）；为空时按 model 自行创建。
    """
    if provider is None:
        provider = ProviderRegistry.for_model(model)
    system_text = (
        f"你是一名独立的「{cfg['name']}」子智能体，作为专业创作助手参与协作。\n"
        f"{cfg['prompt']}\n"
        "你有独立任务，需要独自完成产出：专注、专业、直接给出成果本身，"
        "不要复述任务、不要寒暄、不要解释你在做什么。"
        "如有需要可结合参考图理解。输出控制在 1200 字以内，简体中文。"
    )
    if history:
        system_text += (
            "\n\n【本次创作会话历史】以下是你在本次会话中历次「任务→产出」记录（较早在前）。"
            "请保持设定、角色视角与创作风格连续，可基于历史设定继续推进；不要复述历史内容。"
        )
    messages: list[dict] = [{"role": "system", "content": system_text}]
    for h in history or []:
        messages.append({"role": h["role"], "content": h["content"]})
    content: list[dict] = [{"type": "text", "text": task}]
    for u in (refs or []):
        if u:
            content.append({"type": "image_url", "image_url": {"url": u}})
    messages.append({"role": "user", "content": content})
    resp = provider.chat(messages)
    out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    out = out.strip()
    if not out:
        raise ValueError(f"{cfg['name']}子智能体未返回有效内容")
    return out[:1200]


def _run_subagents_parallel(
    db: Session, session_id, model, ref_images: list[str], entries: list[dict],
    timeout_budget: int = _CHAT_TOTAL_TIMEOUT,
) -> list[dict]:
    """并发执行多个子智能体调用（P9 并行化：ThreadPoolExecutor）。

    - 角色配置 / 模型 / 独立会话历史在进入线程池前预加载（SQLAlchemy Session 非线程安全）
    - provider 在主线程预创建后传入 worker（worker 不触碰 ORM model）
    - 整个并行组受 timeout_budget 约束（as_completed 超时，超时项标记失败）
    - 成功轮次在池返回后于主线程持久化到独立会话历史
    - 返回与 entries 顺序一致的结果 dict 列表（ok/message/params）
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout, as_completed

    from app.providers.errors import map_to_chinese
    from app.services.agent.engine.parsing import _parse_args as _pa  # noqa: F401 兼容名由 executor 注入

    role_cfgs = _load_role_configs(db)
    if model is None:
        model = _resolve_chat_model(db, None)
    # provider 主线程预创建（httpx.Client 线程安全，可多 worker 共享；避免跨线程碰 ORM model）
    provider = ProviderRegistry.for_model(model)
    refs = [u for u in (ref_images or []) if u]
    # 独立会话历史：主线程预加载，避免 worker 访问 Session
    histories = {
        e["function"]["name"].removeprefix("subagent_"): _load_subagent_history(db, session_id, e["function"]["name"].removeprefix("subagent_"))
        for e in entries
    }

    def _parse_args(arguments: str) -> dict:
        import json
        if isinstance(arguments, dict):
            return arguments
        try:
            return json.loads(arguments or "{}") if isinstance(arguments, str) else {}
        except Exception:
            return {}

    def _work(entry: dict) -> dict:
        name = entry["function"]["name"]
        role_key = name.removeprefix("subagent_")
        args = _parse_args(entry["function"]["arguments"])
        task = (args.get("task") or "").strip()
        try:
            cfg = role_cfgs.get(role_key)
            if not cfg:
                raise ValueError(f"未知子智能体：{role_key}")
            if not task:
                raise ValueError("子智能体任务描述不能为空")
            text = _run_subagent_core(cfg, task, refs=refs, model=model, history=histories.get(role_key), provider=provider)
            return {"ok": True, "message": text, "params": args, "role_key": role_key, "task": task}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"工具执行失败：{map_to_chinese(e)}", "params": args, "role_key": role_key, "task": task}

    results: list[dict | None] = [None] * len(entries)
    with ThreadPoolExecutor(max_workers=min(len(entries), 4)) as pool:
        fmap = {pool.submit(_work, e): i for i, e in enumerate(entries)}
        try:
            for fut in as_completed(fmap, timeout=max(timeout_budget, 10)):
                results[fmap[fut]] = fut.result()
        except FutTimeout:
            logger.warning("子智能体并行执行超时（预算 %ss），未完成任务标记失败", timeout_budget)
            for fut, i in fmap.items():
                if not fut.done():
                    fut.cancel()
                    results[i] = {"ok": False, "message": "子智能体执行超时", "params": {}}
    # 兜底：任何未填充的结果（异常路径）标记失败
    for i, r in enumerate(results):
        if r is None:
            results[i] = {"ok": False, "message": "子智能体执行失败", "params": {}}
    # 持久化成功轮次（主线程写库，避免跨线程 Session 冲突）
    for r in results:
        if r.get("ok") and r.get("role_key") and r.get("task"):
            _save_subagent_turn(db, session_id, r["role_key"], r["task"], r["message"])
    return results


def _run_skill(db: Session, skill_name: str, task: str, refs: list[str] | None = None, model=None) -> str:
    """执行可调用 Skill（tool_type=prompt）：按 Skill 提示词作为独立 system 指令生成产出。

    与子智能体类似：独立上下文窗口，不注入主对话历史；Skill 的 prompt 决定执行方式。
    输出非流式，最长约 1500 字。
    """
    from app.models.agent import AgentSkill
    skill = db.scalar(select(AgentSkill).where(AgentSkill.name == skill_name))
    if not skill or not skill.enabled or skill.tool_type != "prompt":
        raise ValueError(f"Skill「{skill_name}」不存在或不可调用")
    if model is None:
        model = _resolve_chat_model(db, None)
    provider = ProviderRegistry.for_model(model)
    content: list[dict] = [{"type": "text", "text": task}]
    for u in (refs or []):
        if u:
            content.append({"type": "image_url", "image_url": {"url": u}})
    resp = provider.chat(
        [
            {
                "role": "system",
                "content": (
                    f"你正在执行「{skill.name}」技能，请严格按照技能说明产出结果。\n"
                    f"{skill.prompt}\n"
                    "直接给出成果本身，不要复述任务、不要寒暄、不要解释你在做什么。"
                    "输出控制在 1500 字以内，简体中文。"
                ),
            },
            {"role": "user", "content": content},
        ]
    )
    out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    out = out.strip()
    if not out:
        raise ValueError(f"Skill「{skill.name}」未返回有效内容")
    return out[:1500]


def _collect_script_context(db: Session, session_id) -> str:
    """收集会话中的剧本创作内容（主消息 + 子智能体产出），供创建项目时结构化整理。

    多版本处理：剧本可能被「重新生成/重写」多次，新旧版本在会话中并存。
    - 子智能体产出（剧本/分镜/视觉设定）是创作核心：每角色最近 2 轮，最新版排最前并标记
      【最新版，以此为准】，保证在 12000 字截断预算内优先保留最新版本，
      避免旧版残留导致项目落库到旧剧本。
    - 主会话最近消息按时间顺序追加，作为补充上下文（排在子智能体产出之后，被截断时先丢）。
    """
    from app.models.agent import AgentSubagentMessage
    from app.services.agent.engine.sessions import list_messages

    parts: list[str] = []
    # 1) 子智能体产出：每角色最近 2 轮，最新在前
    for role_key, label in (("screenwriter", "编剧"), ("director", "导演"), ("artist", "美术")):
        rows = db.scalars(
            select(AgentSubagentMessage)
            .where(
                AgentSubagentMessage.session_id == session_id,
                AgentSubagentMessage.role_key == role_key,
                AgentSubagentMessage.role == "assistant",
            )
            .order_by(AgentSubagentMessage.created_at.desc(), AgentSubagentMessage.id.desc())
            .limit(2)
        ).all()
        for i, r in enumerate(rows):
            tag = "【最新版，以此为准】" if i == 0 else "【较早版本，仅作参考】"
            parts.append(f"{tag} {label}子智能体产出\n{(r.content or '')[:12000]}")
    # 2) 主会话最近消息（按时间顺序，补充上下文）
    for m in list_messages(db, session_id)[-8:]:
        if m.role == "user" and (m.content or "").strip():
            parts.append(f"【用户】\n{m.content[:1500]}")
        elif m.role == "assistant" and (m.content or "").strip():
            parts.append(f"【助手产出】\n{m.content[:12000]}")
    return "\n\n".join(parts)[:60000]
