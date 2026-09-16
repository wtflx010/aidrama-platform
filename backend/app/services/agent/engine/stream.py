"""引擎内核层 · SSE 流式对话主循环（chat_stream）。

从 agent_service.py 剥离（原行号 1890~2777 区域），逻辑未改动：
- `chat_stream`：SSE 流式对话生成器（含工具调用循环 / 占位消息增量落库 / 断连兜底）
- `_extract_media_urls`：从工具结果文本提取媒体直链

工具执行经 `tools/executor.execute_tool`（注册表分发），不再走 agent_service。
"""

from typing import Any

import logging
import re
import time
import threading
import queue
import json
import asyncio

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings as _settings
from app.database import SessionLocal
from app.models.agent import AgentMessage, AgentSession
from app.models.model_config import Model
from app.providers.errors import ProviderError, map_to_chinese
from app.providers.registry import ProviderRegistry
from .chunks import _accumulate_tool_calls, _extract_delta, _extract_reasoning
from .constants import _CHAT_TOTAL_TIMEOUT, _MAX_TOOL_ROUNDS, _SUBAGENT_TOOLS, _TOOLCALL_TEXT_RE
from .context import (
    _auto_generate_title,
    _build_context_messages,
    _ctx_tokens,
    _digest_attachments,
    _resolve_context_refs,
)
from .events import _sse
from .models import _resolve_chat_model
from .sessions import create_session, list_messages
from ..memory.store import (
    _handle_memory,
    _has_memory_instruction,
    _spawn_auto_remember,
    _spawn_creative_decision_memory,
)
from ..tools.executor import _execute_tool, _load_dynamic_tools, _save_tool_message
from ..tools.meta import _ensure_builtin_plugins, _load_plugin_prompts, _load_role_configs
from ..creative.subagent import _maybe_track_main_creative, _run_subagents_parallel

logger = logging.getLogger(__name__)


def _extract_media_urls(text: str) -> list[str]:
    """从工具结果文本中提取 http(s) 媒体直链（static/media 图片/视频/音频）。"""
    urls = re.findall(r"https?://[^\s)\"'<>\[\]]+\.(?:png|jpe?g|webp|gif|mp4|webm|mp3|wav)(?:\?[^\s)\"'<>\[\]]*)?", text)
    return urls[:5]






def chat_stream(payload):
    """SSE 流式对话生成器（含工具调用循环）。payload: AgentChatRequest。

    生成器生命周期内自持 db session（端点依赖注入的 db 在响应返回前会关闭）。
    """
    db = SessionLocal()
    # 断连兜底保存：客户端中途断开（切换会话/停止触发 GeneratorExit）时，
    # 把最后已生成但未持久化的回复保存为 assistant 消息（含思考过程），
    # 避免「切走即 abort」导致整轮回复丢失、前端回放看不到
    last_collected: list[str] = []
    last_thinking: list[str] = []
    saved_ok = False
    # 流式增量落库（5.13）：assistant 消息先创建占位（completed=False）边生成边更新，
    # 断开/异常时 DB 中已保留部分内容（含思考），前端切回展示「思考中」并轮询直至完成
    placeholder_id: str | None = None
    try:
        # 1. 会话解析/创建
        session: AgentSession
        if payload.session_id:
            session = db.get(AgentSession, payload.session_id)
            if not session:
                raise ValueError("会话不存在")
        else:
            session = create_session(db)
        # 1.5 重新生成：删除目标 assistant 回复及其后所有消息，复用其前一条用户消息重新生成
        message_text = (payload.message or "").strip()
        if payload.regenerate_message_id:
            target = db.get(AgentMessage, payload.regenerate_message_id)
            if not target or target.session_id != session.id or target.role != "assistant":
                raise ValueError("重新生成目标不存在")
            prev = db.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.session_id == session.id,
                    AgentMessage.role == "user",
                    AgentMessage.created_at < target.created_at,
                )
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
                .limit(1)
            )
            if not prev:
                raise ValueError("找不到要重新生成的用户消息")
            message_text = (prev.content or "").strip()
            # 删除目标回复及其后的消息，并清理本轮产生的旧工具消息
            # （工具消息创建于 prev 之后、target 之前，若不清除会以「用户消息」回放进新上下文污染结果）
            db.query(AgentMessage).filter(
                AgentMessage.session_id == session.id,
                AgentMessage.created_at >= target.created_at,
            ).delete(synchronize_session=False)
            db.query(AgentMessage).filter(
                AgentMessage.session_id == session.id,
                AgentMessage.role == "tool",
                AgentMessage.created_at >= prev.created_at,
            ).delete(synchronize_session=False)
            # 重新生成 = 重新走工具调用，清理该会话的子智能体独立历史，避免旧产出干扰
            from app.models.agent import AgentSubagentMessage, AgentCreativeState

            db.query(AgentSubagentMessage).filter(
                AgentSubagentMessage.session_id == session.id,
            ).delete(synchronize_session=False)
            # P1 状态层：一并清空创作状态卡，避免旧版本残留污染后续项目创建
            db.query(AgentCreativeState).filter(
                AgentCreativeState.session_id == session.id,
            ).delete(synchronize_session=False)
            db.commit()
        elif not message_text:
            raise ValueError("消息内容不能为空")
        # P8 Phase 5 多模态附件：把附件消化结果注入对话（图片/视频帧→视觉理解；音频转写/文档文本→拼接进消息）
        attachment_images, attachment_text_parts = _digest_attachments(payload.attachments)
        if attachment_images:
            payload.images = [*payload.images, *attachment_images]
        if attachment_text_parts:
            message_text = "\n\n".join([message_text] + attachment_text_parts) if message_text else "\n\n".join(attachment_text_parts)
        # 2. 存用户消息（重新生成时复用原消息，不重复插入）
        #    项目级记忆：从 @ 引用中取首个 project 作为记忆归属项目
        mem_project_id = None
        for ref in (payload.context_refs or []):
            if ref.type == "project":
                mem_project_id = str(ref.id)
                break
        if not payload.regenerate_message_id:
            user_msg = AgentMessage(
                session_id=session.id, role="user", content=message_text,
                images=[u for u in (payload.images or []) if u] or None,
                # P9 多模态附件持久化：随用户消息入库，历史回放时重新注入视觉/转写/文本
                attachments=[a for a in (payload.attachments or []) if a] or None,
            )
            db.add(user_msg)
            db.commit()
            _handle_memory(db, message_text, project_id=mem_project_id)
        # 3. 解析对话模型
        model = _resolve_chat_model(db, payload.model_id)
        # 4. 构建上下文（历史过长时先摘要裁剪 + @引用注入 + 角色人设 + 项目记忆）
        history = list_messages(db, session.id)
        context_text = _resolve_context_refs(db, payload.context_refs or [])
        messages = _build_context_messages(
            db, session, history, model,
            role=payload.agent_role, context_text=context_text, project_id=mem_project_id,
        )
        # 4.2 生成插件：确保内置插件存在，并把选中的插件执行约束注入 system prompt
        #     （对齐 TraeWork「选择插件 → 描述需求 → 执行」）
        _ensure_builtin_plugins(db)
        plugin_prompts = _load_plugin_prompts(db, payload.plugins)
        if plugin_prompts and messages and messages[0].get("role") == "system":
            messages[0]["content"] = (
                messages[0]["content"]
                + "\n\n【已启用插件】\n"
                + "\n".join(f"- {p}" for p in plugin_prompts)
            )
        # 4.3 参考图依赖校验：选中 i2i / i2v / multi_ref 插件但未携带参考图时，
        #     明确提示用户上传图片（避免模型空转或产出不符预期）
        if payload.plugins and not any(payload.images or []):
            from app.models.agent import AgentPlugin
            need_ref = list(
                db.scalars(
                    select(AgentPlugin.name).where(
                        AgentPlugin.name.in_(list(payload.plugins)),
                        AgentPlugin.mode.in_(("i2i", "i2v", "multi_ref")),
                        AgentPlugin.enabled.is_(True),
                    )
                ).all()
            )
            if need_ref:
                raise ValueError(
                    f"插件「{'、'.join(need_ref)}」需要参考图：请在输入框上方上传一张或多张图片后再发送。"
                )
        # 4.5 AI 自动记忆：检测用户消息中的长期偏好（仅在无显式记忆指令时）。
        #     后台线程执行，避免额外 LLM 调用阻塞对话首响应（延迟约 1-2s）
        if not payload.regenerate_message_id and not _has_memory_instruction(message_text):
            _spawn_auto_remember(message_text, model.id, project_id=mem_project_id)
        # 5. 工具调用循环（最多 _MAX_TOOL_ROUNDS 轮）
        provider = ProviderRegistry.for_model(model)
        used_tools: dict[str, int] = {}  # 工具名 → 已执行次数（失败可换参数重试一次，成功则不再调用）
        succeeded_tools: set[str] = set()  # 已成功执行过的工具（禁止再次调用）
        tool_ok = False  # 本轮请求内是否至少有一个工具成功执行过
        tool_fallback = False  # 当前模型不支持工具调用 → 降级为纯文本模式
        text_retry = 0  # 纯文本轮输出工具调用标记时强制重试的次数
        # 已通过错误/耗尽路径收尾（_cleanup_on_error 已把占位消息置 completed=True）：
        # 置位后 finally 兜底不再覆盖，避免「工具执行后无最终回复」停在 completed=False
        terminated = False
        active_roles = _load_role_configs(db)  # DB 自定义 + 内置角色（含工具白名单）
        dynamic_tools = _load_dynamic_tools(db)  # 内置 + Skill + MCP 工具（循环外加载一次）
        # P9 会话级工具白名单：非空列表时仅注册白名单内工具（模型看不到/调不到白名单外工具）
        session_allowed_tools: set[str] | None = None
        if session.tool_whitelist:
            session_allowed_tools = set(session.tool_whitelist)
            dynamic_tools = [
                t for t in dynamic_tools if t["function"]["name"] in session_allowed_tools
            ]
        _chat_started = time.monotonic()  # 单次对话总时长起点（防卡死）
        # 占位 assistant 消息：边生成边增量落库（含思考），断开/异常时 DB 已有部分内容
        placeholder = AgentMessage(
            session_id=session.id, role="assistant",
            content="", thinking=None, completed=False,
        )
        db.add(placeholder)
        db.commit()
        placeholder_id = str(placeholder.id)

        flush_count = 0
        last_flush_ts = time.monotonic()

        def _flush_partial() -> None:
            """把当前已生成正文/思考增量写入占位消息（节流：每 30 个事件或 2 秒一次）。"""
            nonlocal flush_count, last_flush_ts
            flush_count += 1
            now = time.monotonic()
            if flush_count < 30 and now - last_flush_ts < 2.0:
                return
            flush_count = 0
            last_flush_ts = now
            try:
                cur = db.get(AgentMessage, placeholder_id)
                if cur is None:
                    return
                cur.content = "".join(last_collected) or ""
                cur.thinking = "".join(last_thinking) or None
                db.commit()
            except Exception:  # noqa: BLE001
                logger.warning("流式增量落库失败", exc_info=True)

        def _cleanup_on_error() -> None:
            """异常/超时结束：占位消息有内容则标记完成保留，否则删除（避免空消息残留）。"""
            if placeholder_id is None:
                return
            cur = db.get(AgentMessage, placeholder_id)
            if cur is None:
                return
            partial = "".join(last_collected).strip()
            thinking = "".join(last_thinking).strip()
            if partial or thinking:
                cur.content = partial or ""
                cur.thinking = thinking or None
                cur.completed = True
            else:
                db.delete(cur)
            db.commit()

        for _round in range(_MAX_TOOL_ROUNDS):
            # 总时长保护：整次对话（含所有工具链/模型轮）超过上限立即终止，
            # 避免「工具链过长 + 模型持续输出」时用户端无限等待
            if time.monotonic() - _chat_started > _CHAT_TOTAL_TIMEOUT:
                _cleanup_on_error()
                terminated = True
                yield _sse({
                    "type": "error",
                    "message": f"对话处理时间过长（> {_CHAT_TOTAL_TIMEOUT} 秒），已自动停止。"
                    "请拆分请求或稍后重试。",
                })
                return
            # 本轮正文/思考收集（函数级变量：断开兜底保存时可取到最后内容）
            last_collected.clear()
            last_thinking.clear()
            tool_calls_acc: dict[int, dict] = {}
            finish_reason: str | None = None
            try:
                # 降级后不再传 tools 参数，避免不支持 function calling 的模型持续报错
                for chunk in provider.chat_stream(
                    messages, tools=None if tool_fallback else dynamic_tools,
                ):
                    # 推理模型思考过程（reasoning_content）单独透传前端展示，不进正文
                    reasoning = _extract_reasoning(chunk)
                    if reasoning:
                        last_thinking.append(reasoning)
                        yield _sse({"type": "thinking", "content": reasoning})
                        _flush_partial()
                    delta = _extract_delta(chunk)
                    if delta:
                        last_collected.append(delta)
                        yield _sse({"type": "token", "content": delta})
                        _flush_partial()
                    _accumulate_tool_calls(chunk, tool_calls_acc)
                    choices = chunk.get("choices") or []
                    if choices and choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
            except Exception as e:
                err_low = str(e).lower()
                # 工具参数不被支持（Provider 报 tools/function 相关错误）→ 降级纯文本重试一次
                if not tool_fallback and any(
                    k in err_low for k in ("tool", "function", "tool_calls")
                ):
                    tool_fallback = True
                    # 回退到最后一个工具调用之前（去掉未成对的 tool_calls/tool 消息）
                    idx = None
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].get("role") == "assistant" and "tool_calls" in messages[i]:
                            idx = i
                            break
                    if idx is not None:
                        messages = messages[:idx]
                    messages.append({
                        "role": "system",
                        "content": "当前模型不支持工具调用，请直接用文本回答用户，不要调用任何工具。",
                    })
                    continue
                err = str(e) if isinstance(e, ProviderError) else map_to_chinese(e)
                logger.exception("创作助手对话失败: %s", err)
                _cleanup_on_error()
                yield _sse({"type": "error", "message": err})
                return
            text_so_far = "".join(last_collected)
            # 需要调工具？
            if finish_reason == "tool_calls" and tool_calls_acc:
                # 追加 assistant 消息（含 tool_calls，OpenAI 协议要求）
                assistant_tool_calls = [
                    {
                        "id": entry["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": entry["name"], "arguments": entry["arguments"]},
                    }
                    for i, entry in tool_calls_acc.items()
                ]
                messages.append({
                    "role": "assistant",
                    "content": text_so_far or None,
                    "tool_calls": assistant_tool_calls,
                })
                # 逐个执行工具（同一工具每请求最多执行一次，防模型重复调用浪费）
                executed_this_round = False
                executed_results: list[str] = []
                # 第一遍：白名单/去重校验，收集本轮可执行的调用
                # （P9 并行化：子智能体组内并发执行，其余工具保持顺序）
                executable: list[dict] = []
                for entry in assistant_tool_calls:
                    name = entry["function"]["name"]
                    # 角色工具白名单：当前创作角色无权调用的工具直接拒绝并提示模型改用文本
                    role_cfg = active_roles.get(payload.agent_role or "")
                    if role_cfg and role_cfg["tools"] and name not in role_cfg["tools"]:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": entry["id"],
                            "content": (
                                f"当前角色（{role_cfg['name']}）不允许调用工具「{name}」，"
                                "请直接用文本回答用户。"
                            ),
                        })
                        continue
                    # P9 会话级工具白名单：调用时二次校验（防止越权/白名单调整后的残留调用）
                    if session_allowed_tools is not None and name not in session_allowed_tools:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": entry["id"],
                            "content": (
                                f"当前会话未授权调用工具「{name}」（工具白名单外），"
                                "请直接用文本回答用户。"
                            ),
                        })
                        continue
                    if name in succeeded_tools or used_tools.get(name, 0) >= 2:
                        # 重复调用（已成功过 / 失败已重试满 2 次）：不执行、不持久化，
                        # 仅提示模型基于已有结果作答或换其他工具
                        tool_ok = True
                        messages.append({
                            "role": "tool",
                            "tool_call_id": entry["id"],
                            "content": (
                                f"工具「{name}」已达本次请求的调用上限（成功过或已重试失败）。"
                                "请基于已有结果回答，或换用其他工具继续。"
                            ),
                        })
                        continue
                    used_tools[name] = used_tools.get(name, 0) + 1
                    executed_this_round = True
                    executable.append(entry)
                # P9 并行化：同一轮 ≥2 个子智能体调用 → 线程池并发执行，结果按调用顺序汇总
                ref_images = [u for u in (payload.images or []) if u]
                subagent_group = [e for e in executable if e["function"]["name"] in _SUBAGENT_TOOLS]
                other_group = [e for e in executable if e["function"]["name"] not in _SUBAGENT_TOOLS]
                if len(subagent_group) >= 2:
                    # 先落库 running 占位消息（切走/回放时子智能体任务不丢失），
                    # 并行执行完成后回填结果；前端按 tool_status=running 轮询刷新
                    sub_msg_ids: dict[str, Any] = {}
                    for _e in subagent_group:
                        _sub_name = _e["function"]["name"]
                        _ph = AgentMessage(
                            session_id=session.id, role="tool", tool_name=_sub_name,
                            tool_status="running", content="",
                        )
                        db.add(_ph)
                        db.flush()
                        sub_msg_ids[_sub_name] = _ph.id
                    db.commit()
                    # 全部置为 running（并行执行中，前端可见多个子智能体卡片）
                    for _e in subagent_group:
                        yield _sse({
                            "type": "tool", "name": _e["function"]["name"],
                            "status": "running", "step": "子智能体并行思考中…",
                            "parallel": True,
                        })
                    sub_results = _run_subagents_parallel(
                        db, session.id, model, ref_images, subagent_group,
                        timeout_budget=max(int(_CHAT_TOTAL_TIMEOUT - (time.monotonic() - _chat_started)), 30),
                    )
                    if any(_r.get("ok") for _r in sub_results):
                        tool_ok = True
                    for _e, _r in zip(subagent_group, sub_results):
                        _sub_name = _e["function"]["name"]
                        if _r.get("ok"):
                            yield _sse({"type": "tool", "name": _sub_name, "status": "succeeded"})
                        else:
                            yield _sse({
                                "type": "tool", "name": _sub_name, "status": "failed",
                                "message": _r.get("message", ""),
                            })
                        _save_tool_message(db, session.id, _sub_name, _r, msg_id=sub_msg_ids.get(_sub_name))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": _e["id"],
                            "content": _r.get("message", ""),
                        })
                        if _r.get("ok"):
                            succeeded_tools.add(_sub_name)
                            executed_results.append(
                                f"[工具 {_sub_name} 执行结果]\n{_r.get('message', '')}"
                            )
                # 其余工具按顺序执行；不足 2 个的子智能体（0 或 1 个）并入顺序路径
                # （≥2 个已在上面并行执行，不再进入本循环）
                draft_episode_count = 0  # 同轮 create_project 草案已含的集数（会话已有剧本整理而来）
                for entry in other_group + (subagent_group if len(subagent_group) < 2 else []):
                    name = entry["function"]["name"]
                    # 先落库 running 占位消息（切走/回放时工具任务不丢失），完成后回填结果
                    _ph = AgentMessage(
                        session_id=session.id, role="tool", tool_name=name,
                        tool_status="running", content="",
                    )
                    db.add(_ph)
                    db.commit()
                    db.refresh(_ph)
                    tool_msg_id = _ph.id
                    try:
                        result, events = _execute_tool(
                            db, session.id, name, entry["function"]["arguments"],
                            ref_images=ref_images, model=model,
                            timeout_budget=max(int(_CHAT_TOTAL_TIMEOUT - (time.monotonic() - _chat_started)), 30),
                        )
                        tool_ok = True
                        for ev in events:
                            yield _sse(ev)
                        # 通用完成事件：无 media/project/异步任务/pending 确认事件时补发 succeeded，
                        # 让卡片结束"执行中"状态（原仅 code_/git_ 特判，P9 推广到全部同步工具；
                        # pending 类如 git_commit/git_push 需等用户前端确认，不能补发）
                        if not any(
                            ev.get("type") in ("media", "project")
                            or ev.get("draft_id") or ev.get("task_id")
                            or ev.get("status") == "pending"
                            for ev in events
                        ):
                            yield _sse({
                                "type": "tool", "name": name, "status": "succeeded",
                                "message": result.get("message", ""),
                            })
                    except Exception as e:
                        result = {
                            "ok": False,
                            "message": f"工具执行失败：{map_to_chinese(e)}",
                        }
                        # 工具失败也推送事件（此前 tool running 事件随异常丢失，前端无感知）
                        yield _sse({
                            "type": "tool", "name": name, "status": "failed",
                            "message": map_to_chinese(e),
                        })
                    # 持久化工具消息（更新 running 占位为最终状态）
                    _save_tool_message(db, session.id, name, result, msg_id=tool_msg_id)
                    # 回填给模型
                    messages.append({
                        "role": "tool",
                        "tool_call_id": entry["id"],
                        "content": result.get("message", ""),
                    })
                    if result.get("ok"):
                        succeeded_tools.add(name)
                        executed_results.append(f"[工具 {name} 执行结果]\n{result.get('message', '')}")
                # 工具成功执行后注入 system 结果指令。不强制纯文本轮：
                # 保留 tools 允许模型跨轮调用其他工具完成链式调研
                # （如 web_search 搜索 → web_fetch 读详情页；失败后换 raw 直链重试）。
                if executed_results:
                    messages.append({
                        "role": "system",
                        "content": (
                            "以下是系统回填的工具执行结果：\n\n"
                            + "\n\n".join(executed_results)
                            + "\n\n基于这些结果：若已足够回答用户，请直接用简体中文给出最终回答；"
                            "若还需补充信息，可调用其他工具继续（如用 web_fetch 打开链接读取详情、"
                            "或换用 raw 直链/移动版等不同 URL 形式重试），但不要重复调用已成功的工具。"
                        ),
                    })
                if not executed_this_round:
                    # 本轮全是重复/越权工具调用（模型空转）：禁止再调工具，
                    # 强制基于已有结果用文本总结（最多强制 2 次，防死循环）
                    if text_retry < 2:
                        text_retry += 1
                        messages.append({
                            "role": "system",
                            "content": (
                                "你上一轮输出的工具调用均未被执行（工具已达调用上限或当前角色不允许）。"
                                "请停止调用任何工具，直接基于已注入的工具执行结果，"
                                "用简体中文完整回答用户的问题；若确实信息不足，"
                                "如实说明并给出替代建议（如换种提问方式、稍后重试）。"
                            ),
                        })
                        continue
                    full = text_so_far.strip() or (
                        "已完成工具调用，但由于信息不足未能完整回答。"
                        "你可以换个方式提问，或让我换个途径再试一次。"
                    )
                    # 更新占位消息为最终回复（completed=True），而非新增
                    # 占位可能在并发 regenerate/fork 清理时已被删除：跳过写库仍正常收尾
                    cur = db.get(AgentMessage, placeholder_id)
                    if cur is not None:
                        cur.content = full
                        cur.thinking = "".join(last_thinking) or None
                        cur.completed = True
                    saved_ok = True
                    db.commit()
                    _auto_generate_title(db, session, model)
                    yield _sse({
                        "type": "done", "message_id": str(placeholder_id),
                        "context_tokens": _ctx_tokens(
                            list_messages(db, session.id),
                            system_text=(messages[0].get("content") or "") if messages else "",
                        ),
                    })
                    return
                if not tool_ok:
                    # 工具全部失败：降级为纯文本模式（不再传 tools），
                    # 让模型如实告知失败原因并给出可行的替代建议（对齐 TraeWork 不放弃原则）
                    tool_fallback = True
                    messages.append({
                        "role": "system",
                        "content": (
                            "本轮调用的工具均执行失败。请用简体中文如实告知用户失败原因，"
                            "并给出可行的替代建议（如换用其他工具、换 URL 形式、稍后重试）。"
                            "不要继续调用工具。"
                        ),
                    })
                    continue
                # 继续下一轮（模型基于工具结果生成最终回复）
                continue
            # 无工具调用：保存最终回复
            full = text_so_far or "（已完成）"
            # 纯文本轮中模型可能仍以 <tool_call>/<function= 文本形式模拟工具调用
            # （部分模型拿到工具结果后仍想继续调用）。检测到标记时清洗并强制重试总结。
            if _TOOLCALL_TEXT_RE.search(full) and text_retry < 2:
                text_retry += 1
                messages.append({
                    "role": "system",
                    "content": (
                        "你上一轮输出了工具调用标记（如 <tool_call>、<function=、<parameter=），"
                        "但本轮工具已全部执行完毕、不允许再调用任何工具。"
                        "请忽略之前的标记输出，直接用简体中文自然语言、"
                        "基于已注入的工具执行结果回答用户的问题。"
                    ),
                })
                continue
            full = _TOOLCALL_TEXT_RE.sub("", full).strip() or "（已完成）"
            # 更新占位消息为最终回复（completed=True），而非新增
            # 占位可能在并发 regenerate/fork 清理时已被删除：跳过写库仍正常收尾
            cur = db.get(AgentMessage, placeholder_id)
            if cur is not None:
                cur.content = full
                cur.thinking = "".join(last_thinking) or None
                cur.completed = True
            saved_ok = True
            db.commit()
            # P2 主对话剧本追踪：主对话直接产出的剧本写入创作状态卡（role=main），
            # 创建项目时与编剧子智能体版本合并、取最新者
            _maybe_track_main_creative(db, session.id, message_text, full)
            # P3 创作决策记忆：后台提炼这轮对话中值得长期记住的创作决策
            # （否决/确定/修正设定、风格、主角名等），跨会话保持一致
            if not payload.regenerate_message_id:
                _spawn_creative_decision_memory(
                    message_text, full, model.id, project_id=mem_project_id,
                )
            _auto_generate_title(db, session, model)
            yield _sse({
                "type": "done", "message_id": str(placeholder_id),
                "context_tokens": _ctx_tokens(
                    list_messages(db, session.id),
                    system_text=(messages[0].get("content") or "") if messages else "",
                ),
            })
            return
        # 工具轮次耗尽：报错
        _cleanup_on_error()
        terminated = True
        yield _sse({"type": "error", "message": "工具调用次数过多，已停止"})
    except ValueError as e:
        # 业务校验类错误（参数不合法/资源不存在等）：直接展示给用户
        if placeholder_id is not None:
            _cleanup_on_error()
            terminated = True
        yield _sse({"type": "error", "message": str(e)})
    except Exception as e:  # noqa: BLE001
        # 兜底：任何未预期异常都不允许中断 SSE 流（前端会无限等待/白屏），
        # 统一转成 error 事件结束本轮对话，并记录日志便于排查
        logger.exception("创作助手对话发生未预期异常，已安全结束")
        if placeholder_id is not None:
            _cleanup_on_error()
            terminated = True
        yield _sse({"type": "error", "message": "系统处理出错，请稍后重试或换个说法提问"})
    finally:
        # 断连兜底：客户端中途断开未走到正常保存处时，占位消息已通过流式增量
        # 保留部分内容（含思考）。这里统一收尾：有内容保留（completed=False 供前端
        # 显示「思考中」并轮询；生成器线程若继续跑完会由正常路径置 True），无内容删除。
        # 注意：错误/耗尽路径已由 _cleanup_on_error 置 completed=True（terminated=True），
        # 此处仅处理真正「未收尾就中断」（客户端断开触发 GeneratorExit）的情况，
        # 避免把已完成的消息覆盖回 completed=False 造成「工具执行后无回复」假象。
        if not saved_ok and not terminated and placeholder_id is not None:
            try:
                cur = db.get(AgentMessage, placeholder_id)
                if cur is not None:
                    partial = "".join(last_collected).strip()
                    thinking = "".join(last_thinking).strip()
                    if partial or thinking:
                        cur.content = partial or ""
                        cur.thinking = thinking or None
                        cur.completed = False  # 未完成：前端轮询直至服务端完成或人工重发
                        db.commit()
                        logger.info(
                            "客户端断开，已兜底保留部分回复（%d 字 / thinking %d 字）",
                            len(partial), len(thinking),
                        )
                    else:
                        db.delete(cur)
                        db.commit()
                        logger.info("客户端断开且无内容，已删除占位消息")
            except Exception:  # noqa: BLE001
                logger.warning("断连兜底保存失败", exc_info=True)
        db.close()


