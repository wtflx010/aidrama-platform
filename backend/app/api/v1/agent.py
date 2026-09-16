"""创作助手（Agent）API：会话 CRUD / 消息历史 / SSE 流式对话 / Skill 库。"""
import logging
import queue
import threading
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.agent import (
    AgentChatRequest,
    AgentGoalCreate,
    AgentGoalOut,
    AgentMcpServerCreate,
    AgentMcpServerOut,
    AgentMcpServerUpdate,
    AgentMcpTestResult,
    AgentMemoryCreate,
    AgentMemoryHit,
    AgentMemoryOut,
    AgentMessageOut,
    AgentPlanCreate,
    AgentPlanOut,
    AgentRoleCreate,
    AgentRoleOut,
    AgentRoleUpdate,
    AgentScheduleCreate,
    AgentScheduleOut,
    AgentScheduleUpdate,
    AgentSearchHit,
    AgentSessionCreate,
    AgentSessionOut,
    AgentSkillCreate,
    AgentSkillOut,
    AgentSkillUpdate,
    AgentPluginCreate,
    AgentPluginOut,
    AgentPluginUpdate,
    AgentRuleCreate,
    AgentRuleOut,
    AgentRuleUpdate,
    NovelOutlineBody,
    NovelOutlineOut,
    NovelWriteBody,
    NovelWriteOut,
)
from app.services import agent_service
from app.services import agent_schedule_service

router = APIRouter()

logger = logging.getLogger(__name__)

# 后台对话任务（P5.14）：chat_stream 在独立线程执行，SSE 仅转发事件队列，
# 客户端切换会话/刷新/断开不中断任务（继续完成工具链与落库）。
# 同一会话的任务用锁串行执行，避免多条消息交错污染历史。
_AGENT_TASK_LOCKS: dict[str, threading.Lock] = {}
_AGENT_TASK_LOCKS_GUARD = threading.Lock()
# SSE 事件队列结束哨兵
_SSE_END = object()


def _agent_task_lock(session_id: str) -> threading.Lock:
    with _AGENT_TASK_LOCKS_GUARD:
        lock = _AGENT_TASK_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _AGENT_TASK_LOCKS[session_id] = lock
        return lock


# ─── 会话 ─────────────────────────────────────────────

@router.get("/agent/sessions", response_model=list[AgentSessionOut])
def list_sessions(db: Session = Depends(get_db)):
    return agent_service.list_sessions(db)


@router.get("/agent/sessions/search", response_model=list[AgentSearchHit])
def search_sessions(q: str = "", limit: int = 20, db: Session = Depends(get_db)):
    """跨会话搜索历史消息（P8 Phase 3）：关键词命中消息摘要，可跳转定位。"""
    return agent_service.search_sessions(db, q, limit)


@router.post("/agent/sessions", response_model=AgentSessionOut, status_code=201)
def create_session(payload: AgentSessionCreate, db: Session = Depends(get_db)):
    return agent_service.create_session(db, payload.title)


@router.delete("/agent/sessions/{session_id}")
def delete_session(session_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_session(db, session_id):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


class SessionUpdateBody(BaseModel):
    """P9 会话级工具白名单：None=全部工具可用；空列表=不限制但清空；列表=仅白名单内可用。"""
    tool_whitelist: list[str] | None = None


@router.put("/agent/sessions/{session_id}", response_model=AgentSessionOut)
def update_session(session_id: UUID, payload: SessionUpdateBody, db: Session = Depends(get_db)):
    s = db.get(agent_service.AgentSession, session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    s.tool_whitelist = payload.tool_whitelist
    db.commit()
    db.refresh(s)
    return s


@router.get("/agent/sessions/{session_id}/messages", response_model=list[AgentMessageOut])
def list_messages(session_id: UUID, db: Session = Depends(get_db)):
    if not db.get(agent_service.AgentSession, session_id):
        raise HTTPException(404, "会话不存在")
    return agent_service.list_messages(db, session_id)


# ─── 会话副本 / 上下文压缩 ─────────────────────────────

@router.post("/agent/sessions/{session_id}/fork", response_model=AgentSessionOut, status_code=201)
def fork_session(session_id: UUID, after_message_id: UUID | None = None, db: Session = Depends(get_db)):
    """创建会话副本：复制到指定消息之前的所有消息，继承标题与摘要。"""
    try:
        return agent_service.fork_session(db, session_id, str(after_message_id) if after_message_id else None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/sessions/{session_id}/compact")
def compact_session(session_id: UUID, db: Session = Depends(get_db)):
    """手动压缩上下文：早期对话归档为摘要，保留最近消息。"""
    try:
        return agent_service.compact_session(db, session_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── 提示词优化 ───────────────────────────────────────

class OptimizePromptBody(BaseModel):
    message: str
    model_id: UUID | None = None


class GoalStatusBody(BaseModel):
    status: str  # active / paused / done


class GoalAdvanceBody(BaseModel):
    model_id: UUID | None = None  # 推进用模型（沿用 UI 选择，空则默认）


class GoalEvaluateBody(BaseModel):
    model_id: UUID | None = None  # 自评用模型（沿用 UI 选择，空则默认）


@router.post("/agent/optimize-prompt")
def optimize_prompt(payload: OptimizePromptBody, db: Session = Depends(get_db)):
    """把口语化描述优化为结构化生成指令。"""
    try:
        optimized = agent_service.optimize_prompt(db, payload.message, payload.model_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"optimized": optimized}


# ─── SSE 流式对话 ─────────────────────────────────────

@router.post("/agent/chat")
def chat(payload: AgentChatRequest):
    """SSE 流式对话。事件：token（文本增量）/ thinking / tool（工具执行）/ media / project / done / error。

    对话任务在独立后台线程执行：客户端切换会话/刷新/断开仅中止 SSE 事件推送，
    任务继续完成整轮对话（含工具链）并随生成增量落库（completed 标记）；
    切回会话后历史查询（前端自动轮询）即可看到完成结果。SSE 仅从队列转发事件。
    """
    import time as _t
    logger.info(
        "[agent-chat] 收到请求 session=%s msg_len=%d model=%s ts=%.3f",
        payload.session_id, len((payload.message or "")[:200]), payload.model_id, _t.time(),
    )
    q: queue.Queue = queue.Queue()

    def _run():
        # 同一会话任务串行执行（新会话首次为空，用占位 key）
        lock = _agent_task_lock(payload.session_id or "new")
        with lock:
            try:
                for ev in agent_service.chat_stream(payload):
                    q.put_nowait(ev)
            except Exception:  # noqa: BLE001
                logger.exception("后台对话任务异常")
            finally:
                q.put_nowait(_SSE_END)

    threading.Thread(target=_run, name="agent-chat", daemon=True).start()

    def event_source():
        # 2026-08-16 修复「15 秒无响应头」：StreamingResponse 的响应头在生成器
        # **首次迭代**时才发出。此前首个事件要等后台线程产出（模型首 token，
        # vLLM TTFT 均值 3.7s、长会话上下文可达 15s+）→ 前端在拿到任何字节前
        # 就超时（"请求未送达后端"）。先在开头无条件 yield 一个 start 事件
        # （必须与 _sse() 相同的 data: 字符串格式，dict 会中断流）：
        # 响应头立即发出，前端立刻收到事件（重置空闲计时）；后续事件仍由后台
        # 线程经队列推送，行为不变。
        import json as _json
        _t_start = _t.time()
        yield f"data: {_json.dumps({'type': 'start', 'ts': _t.time()}, ensure_ascii=False)}\n\n"
        while True:
            ev = q.get()
            if ev is _SSE_END:
                break
            yield ev
        # 2026-08-16：完成打点——用户报「思考中不回复」时，用耗时判断是
        # 浏览器侧（没收到请求）还是模型侧（收到但很慢）还是早已完成。
        logger.info(
            "[agent-chat] 流结束 session=%s elapsed=%.1fs",
            payload.session_id, _t.time() - _t_start,
        )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── 项目创建确认（create_project 草案 → 前端确认后落库完整项目）────────

class ConfirmProjectBody(BaseModel):
    message_id: UUID  # create_project 工具消息 id（含 pending_project_draft）
    aspect_ratio: str = "9:16"  # 16:9 / 9:16 / 1:1 / 4:3 / 3:4
    style_id: UUID | None = None  # 预设风格（art_style.id）
    art_style_prompt: str | None = None  # 自定义风格文本（style_id 为空时使用）
    resolution: str | None = None  # 视频分辨率（480p/720p）
    video_params: dict | None = None  # 项目级视频生成参数（fps/res/video_size/steps/cfg/seed/turbo）


@router.post("/agent/projects/confirm")
def confirm_project(payload: ConfirmProjectBody, db: Session = Depends(get_db)):
    """确认创建项目：按用户选择（画幅/风格）把草案落库为完整项目（角色/场景/道具/分镜）。"""
    try:
        p = agent_service.confirm_project(
            db, payload.message_id,
            aspect_ratio=payload.aspect_ratio,
            style_id=payload.style_id,
            art_style_prompt=payload.art_style_prompt,
            resolution=payload.resolution,
            video_params=payload.video_params,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"project_id": str(p.id), "title": p.title, "aspect_ratio": p.aspect_ratio}


class ConfirmProjectDeleteBody(BaseModel):
    message_id: UUID  # project_delete 工具消息 id（含 pending_project_delete）


@router.post("/agent/projects/delete")
def confirm_project_delete(payload: ConfirmProjectDeleteBody, db: Session = Depends(get_db)):
    """确认删除项目：真正删除（含幕/分镜/资产/视频/音频及磁盘文件）。"""
    try:
        result = agent_service.project_delete_confirm(db, payload.message_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


# ─── 创作规划（Plan 工作流）────────────────────────────

@router.post("/agent/plans", response_model=AgentPlanOut, status_code=201)
def create_plan(payload: AgentPlanCreate, db: Session = Depends(get_db)):
    """生成创作规划文档（status=draft 待确认）。"""
    try:
        return agent_service.create_plan(db, payload.session_id, payload.message, payload.model_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/agent/sessions/{session_id}/plans", response_model=list[AgentPlanOut])
def list_plans(session_id: UUID, db: Session = Depends(get_db)):
    if not db.get(agent_service.AgentSession, session_id):
        raise HTTPException(404, "会话不存在")
    return agent_service.list_plans(db, session_id)


@router.get("/agent/plans/{plan_id}", response_model=AgentPlanOut)
def get_plan(plan_id: UUID, db: Session = Depends(get_db)):
    try:
        return agent_service.get_plan(db, plan_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/agent/plans/{plan_id}/confirm", response_model=AgentPlanOut)
def confirm_plan(plan_id: UUID, db: Session = Depends(get_db)):
    """确认规划，进入可执行状态。"""
    try:
        return agent_service.confirm_plan(db, plan_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/plans/{plan_id}/steps/{step_index}/done", response_model=AgentPlanOut)
def mark_plan_step_done(plan_id: UUID, step_index: int, db: Session = Depends(get_db)):
    """标记某一步完成；全部完成后规划自动置为 done。"""
    try:
        return agent_service.mark_plan_step_done(db, plan_id, step_index)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/plans/{plan_id}")
def delete_plan(plan_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_plan(db, plan_id):
        raise HTTPException(404, "规划不存在")
    return {"ok": True}


# ─── 长期记忆管理 ────────────────────────────────────

@router.get("/agent/memories", response_model=list[AgentMemoryOut])
def list_memories(scope: str | None = None, project_id: UUID | None = None, db: Session = Depends(get_db)):
    """记忆列表：scope=global/project 过滤；project_id 过滤项目级记忆。"""
    return agent_service.list_memory_items(db, scope=scope, project_id=project_id)


@router.get("/agent/memories/search", response_model=list[AgentMemoryHit])
def search_memories(
    q: str = "", scope: str | None = None,
    project_id: UUID | None = None, limit: int = 5,
    db: Session = Depends(get_db),
):
    """P9 记忆语义检索：jieba 分词 + TF-IDF 余弦，按相关度排序返回命中记忆。"""
    return agent_service.search_memories(
        db, q, scope=scope,
        project_id=str(project_id) if project_id else None,
        top_n=max(1, min(limit, 20)),
    )


@router.post("/agent/memories", response_model=AgentMemoryOut, status_code=201)
def create_memory(payload: AgentMemoryCreate, db: Session = Depends(get_db)):
    """手动添加记忆。"""
    try:
        return agent_service.create_memory_item(db, payload.content, payload.scope, payload.project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/memories/{memory_id}")
def delete_memory(memory_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_memory_item(db, memory_id):
        raise HTTPException(404, "记忆不存在")
    return {"ok": True}


# ─── 创作目标（Goal 工作流）───────────────────────────

@router.post("/agent/goals", response_model=AgentGoalOut, status_code=201)
def create_goal(payload: AgentGoalCreate, db: Session = Depends(get_db)):
    """设定创作目标（status=active）。"""
    try:
        return agent_service.create_goal(db, payload.session_id, payload.message, payload.model_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/agent/sessions/{session_id}/goals", response_model=list[AgentGoalOut])
def list_goals(session_id: UUID, db: Session = Depends(get_db)):
    if not db.get(agent_service.AgentSession, session_id):
        raise HTTPException(404, "会话不存在")
    return agent_service.list_goals(db, session_id)


@router.get("/agent/goals/{goal_id}", response_model=AgentGoalOut)
def get_goal(goal_id: UUID, db: Session = Depends(get_db)):
    try:
        return agent_service.get_goal(db, goal_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/agent/goals/{goal_id}/advance")
def advance_goal(goal_id: UUID, payload: GoalAdvanceBody, db: Session = Depends(get_db)):
    """Goal 推进一轮：以目标+当前进度为消息，走 SSE 流式对话（含工具调用），完成后前端再调 evaluate。"""
    try:
        g = agent_service.get_goal(db, goal_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if g.status == "done":
        raise HTTPException(400, "目标已达成，无需继续推进")
    payload_req = AgentChatRequest(
        message=agent_service.goal_advance_message(g),
        session_id=g.session_id,
        images=[],
        model_id=payload.model_id,  # 沿用 UI 当前选择的模型，避免回退默认
    )
    return StreamingResponse(
        agent_service.chat_stream(payload_req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/goals/{goal_id}/evaluate", response_model=AgentGoalOut)
def evaluate_goal(goal_id: UUID, payload: GoalEvaluateBody, db: Session = Depends(get_db)):
    """Goal 自评：判断是否达成；未达成则记录进度并累计推进轮数。"""
    try:
        return agent_service.evaluate_goal(db, goal_id, model_id=payload.model_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/goals/{goal_id}/status", response_model=AgentGoalOut)
def set_goal_status(goal_id: UUID, payload: GoalStatusBody, db: Session = Depends(get_db)):
    """更新目标状态：active / paused / done。"""
    try:
        return agent_service.set_goal_status(db, goal_id, payload.status)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── Skill 库 ─────────────────────────────────────────

@router.get("/agent/skills", response_model=list[AgentSkillOut])
def list_skills(enabled_only: bool = False, db: Session = Depends(get_db)):
    return agent_service.list_skills(db, enabled_only=enabled_only)


@router.post("/agent/skills", response_model=AgentSkillOut, status_code=201)
def create_skill(payload: AgentSkillCreate, db: Session = Depends(get_db)):
    try:
        return agent_service.create_skill(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/agent/skills/{skill_id}", response_model=AgentSkillOut)
def update_skill(skill_id: UUID, payload: AgentSkillUpdate, db: Session = Depends(get_db)):
    try:
        return agent_service.update_skill(db, skill_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/skills/{skill_id}")
def delete_skill(skill_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_skill(db, skill_id):
        raise HTTPException(404, "Skill 不存在")
    return {"ok": True}


# ─── 规则（Rules，对齐 TraeWork，2026-08-11）──────────

@router.get("/agent/rules", response_model=list[AgentRuleOut])
def list_rules(project_id: UUID | None = None, db: Session = Depends(get_db)):
    return agent_service.list_rules(db, project_id=project_id)


@router.post("/agent/rules", response_model=AgentRuleOut, status_code=201)
def create_rule(payload: AgentRuleCreate, db: Session = Depends(get_db)):
    try:
        return agent_service.create_rule(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/agent/rules/{rule_id}", response_model=AgentRuleOut)
def update_rule(rule_id: UUID, payload: AgentRuleUpdate, db: Session = Depends(get_db)):
    try:
        return agent_service.update_rule(db, rule_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/rules/{rule_id}")
def delete_rule(rule_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_rule(db, rule_id):
        raise HTTPException(404, "规则不存在")
    return {"ok": True}


# ─── P8 完整编码能力：git 提交确认（模型调用 git_commit 后由前端确认执行）──

class GitCommitBody(BaseModel):
    files: list[str] = []
    message: str


@router.post("/agent/git/commit")
def git_commit(payload: GitCommitBody, db: Session = Depends(get_db)):
    try:
        result = agent_service._git_commit_execute(payload.files, payload.message)
        return {"ok": True, "message": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


class GitPushBody(BaseModel):
    remote: str = "origin"
    branch: str = ""


@router.post("/agent/git/push")
def git_push(payload: GitPushBody, db: Session = Depends(get_db)):
    """真正执行 git push（模型调用 git_push 后由前端确认执行）。"""
    try:
        result = agent_service._git_push_execute(payload.remote, payload.branch)
        return {"ok": True, "message": result}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── P8 Phase 4：TTS 语音输出（前端「朗读」按钮直接调用）──

class TtsSpeakBody(BaseModel):
    text: str
    voice: str = "default"
    emotion: str | None = None


@router.post("/agent/tts")
def tts_speak(payload: TtsSpeakBody, db: Session = Depends(get_db)):
    """把一段文本朗读为语音，返回可播放音频 URL（复用配音 TTS 模型链路）。"""
    from app.providers.errors import map_to_chinese

    try:
        url = agent_service._tool_tts_speak(db, payload.model_dump())
        return {"ok": True, "audio_url": url}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        # TTS Provider 失败（无模型/服务端错误等）统一转中文提示
        raise HTTPException(400, f"语音合成失败：{map_to_chinese(e)}")


@router.get("/agent/tts/health")
def tts_health(db: Session = Depends(get_db)):
    """TTS 可用性检查：模型是否配置 + CosyVoice 服务是否在线（供前端置灰朗读按钮）。"""
    from app.providers.errors import map_to_chinese

    # 1) TTS 模型是否可用（默认+生效，与合成链路同一解析规则）
    from app.models.model_config import ModelType

    model_ok = True
    model_error = ""
    try:
        from app.services.keyframe_service import _resolve_model
        _resolve_model(db, None, ModelType.tts, "voice")
    except ValueError as e:
        model_ok = False
        model_error = str(e)

    # 2) CosyVoice 服务是否在线（无 key 的本地 openai_tts 才需要检查 wrapper）
    service_ok = True
    service_error = ""
    import os

    import httpx as _httpx

    wrapper = os.getenv("COSYVOICE_WRAPPER_URL", "http://localhost:9880")
    try:
        r = _httpx.get(f"{wrapper}/voices", timeout=3)
        r.raise_for_status()
    except Exception as e:
        service_ok = False
        service_error = map_to_chinese(e)

    ok = model_ok and service_ok
    reasons: list[str] = []
    if not model_ok:
        reasons.append(model_error)
    if not service_ok:
        reasons.append("CosyVoice 服务未在线" + (f"（{service_error}）" if service_error else ""))
    return {"ok": ok, "reason": "；".join(reasons)}


# ─── P8 Phase 5：多模态附件上传处理（图片/音频/视频/文档 → 注入对话）──

class AttachmentUploadBody(BaseModel):
    """多模态附件上传（base64，避免 multipart 依赖）。"""
    filename: str
    data_base64: str  # 纯 base64（不带 data: 前缀）


@router.post("/agent/attachments", status_code=201)
def upload_attachment(payload: AttachmentUploadBody, session_id: str = "", db: Session = Depends(get_db)):
    """上传多模态附件并处理：图片→URL；音频→Whisper 转写；视频→抽 3 帧；PDF/Word/文本→提取文字。

    返回结构化结果 dict（kind/name/url/transcript/frames/text），前端随 chat 请求 attachments 字段回传。
    """
    from app.services import agent_attachment_service

    # session_id 为空时用独立命名空间（避免互相覆盖）
    ns = session_id or "preview"
    try:
        result = agent_attachment_service.process_attachment(payload.filename, payload.data_base64, ns)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"附件处理失败：{e}")
    return {"ok": True, "attachment": result}


# ─── P8 定时自动化任务（对齐 Hermes/OpenClaw cron，2026-08-11）──

@router.get("/agent/schedules", response_model=list[AgentScheduleOut])
def list_schedules(db: Session = Depends(get_db)):
    return agent_schedule_service.list_schedules(db)


@router.post("/agent/schedules", response_model=AgentScheduleOut, status_code=201)
def create_schedule(payload: AgentScheduleCreate, db: Session = Depends(get_db)):
    try:
        return agent_schedule_service.create_schedule(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/agent/schedules/{schedule_id}", response_model=AgentScheduleOut)
def update_schedule(schedule_id: UUID, payload: AgentScheduleUpdate, db: Session = Depends(get_db)):
    try:
        row = agent_schedule_service.update_schedule(db, schedule_id, payload)
        if not row:
            raise HTTPException(404, "定时任务不存在")
        return row
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/schedules/{schedule_id}")
def delete_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    if not agent_schedule_service.delete_schedule(db, schedule_id):
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True}


@router.post("/agent/schedules/{schedule_id}/run")
def run_schedule(schedule_id: UUID, db: Session = Depends(get_db)):
    """立即运行一次（调试用）：执行并推进下次触发时间。"""
    try:
        message = agent_schedule_service.run_schedule(db, schedule_id)
        row = agent_schedule_service.get_schedule(db, schedule_id)
        return {"ok": True, "message": message, "schedule": row}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── MCP 服务器配置（2026-08-11）──────────────────────

@router.get("/agent/mcp-servers", response_model=list[AgentMcpServerOut])
def list_mcp_servers(enabled_only: bool = False, db: Session = Depends(get_db)):
    return agent_service.list_mcp_servers(db, enabled_only=enabled_only)


@router.post("/agent/mcp-servers", response_model=AgentMcpServerOut, status_code=201)
def create_mcp_server(payload: AgentMcpServerCreate, db: Session = Depends(get_db)):
    try:
        return agent_service.create_mcp_server(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/agent/mcp-servers/{server_id}", response_model=AgentMcpServerOut)
def update_mcp_server(server_id: UUID, payload: AgentMcpServerUpdate, db: Session = Depends(get_db)):
    try:
        return agent_service.update_mcp_server(db, server_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/mcp-servers/{server_id}")
def delete_mcp_server(server_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_mcp_server(db, server_id):
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True}


@router.post("/agent/mcp-servers/{server_id}/test", response_model=AgentMcpTestResult)
def test_mcp_server(server_id: UUID, db: Session = Depends(get_db)):
    """连接测试：启动 MCP 服务器并发现其工具（供管理界面预览）。"""
    try:
        return agent_service.test_mcp_server(db, server_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── 生成插件（P5，对齐 TraeWork 插件，2026-08-11）────

@router.get("/agent/plugins", response_model=list[AgentPluginOut])
def list_plugins(enabled_only: bool = False, db: Session = Depends(get_db)):
    return agent_service.list_plugins(db, enabled_only=enabled_only)


@router.post("/agent/plugins", response_model=AgentPluginOut, status_code=201)
def create_plugin(payload: AgentPluginCreate, db: Session = Depends(get_db)):
    try:
        return agent_service.create_plugin(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/agent/plugins/{plugin_id}", response_model=AgentPluginOut)
def update_plugin(plugin_id: UUID, payload: AgentPluginUpdate, db: Session = Depends(get_db)):
    try:
        return agent_service.update_plugin(db, plugin_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/agent/plugins/{plugin_id}")
def delete_plugin(plugin_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_plugin(db, plugin_id):
        raise HTTPException(404, "插件不存在")
    return {"ok": True}


# ─── 长篇小说写作（/novel 工作流）────────────────────

@router.post("/agent/novel-outline", response_model=NovelOutlineOut)
def novel_outline(payload: NovelOutlineBody, db: Session = Depends(get_db)):
    """生成小说大纲（供前端预览确认，不落库）。"""
    try:
        return agent_service.generate_novel_outline(
            db, payload.brief, payload.genre, payload.chapters, payload.model_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/novel-write", response_model=NovelWriteOut, status_code=201)
def novel_write(payload: NovelWriteBody, db: Session = Depends(get_db)):
    """创建小说 + 提交后台逐章写作任务（outline 为空时由任务自动规划大纲）。"""
    try:
        novel_id, task_id, title = agent_service.submit_novel_writing(
            db, payload.title, payload.brief, payload.genre,
            payload.chapters, payload.outline, payload.model_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"novel_id": novel_id, "task_id": task_id, "title": title}


# ─── 创作角色配置（可配置创作角色/子智能体，T2）────────

@router.get("/agent/roles", response_model=list[AgentRoleOut])
def list_roles(db: Session = Depends(get_db)):
    return agent_service.list_roles(db)


@router.post("/agent/roles", response_model=AgentRoleOut, status_code=201)
def create_role(payload: AgentRoleCreate, db: Session = Depends(get_db)):
    try:
        return agent_service.create_role(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/agent/roles/{role_id}", response_model=AgentRoleOut)
def update_role(role_id: UUID, payload: AgentRoleUpdate, db: Session = Depends(get_db)):
    try:
        return agent_service.update_role(db, role_id, payload)
    except ValueError as e:
        raise HTTPException(404 if "不存在" in str(e) else 400, str(e))


@router.delete("/agent/roles/{role_id}")
def delete_role(role_id: UUID, db: Session = Depends(get_db)):
    if not agent_service.delete_role(db, role_id):
        raise HTTPException(404, "角色不存在")
    return {"ok": True}
