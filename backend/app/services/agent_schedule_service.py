"""定时自动化任务服务（P8，对齐 Hermes/OpenClaw cron 调度，2026-08-11）。

- cron_expr：5 字段 cron 表达式，用 croniter 解析判断到期与计算下次运行时间
- prompt 类：定时让 LLM 生成内容并写入指定会话（未指定会话则自动新建）
- system 类：执行系统动作（retry_failed_tasks 批量重试失败任务）
- 生成类（生图/生视频）不在定时动作范围：遵守「禁止系统自动触发生成类任务」约束

由 celery beat 每 30s 触发 agent_schedule_tick 扫描到期任务；前端「立即运行」复用 run_schedule。
"""
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentMessage, AgentSchedule, AgentSession
from app.models.task import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)

# 系统动作白名单
SYSTEM_ACTIONS = {"retry_failed_tasks"}
# action_type 白名单
ACTION_TYPES = {"prompt", "system"}


# ─── cron 工具 ─────────────────────────────────────────

def validate_cron(cron_expr: str) -> None:
    """校验 5 字段 cron 表达式是否合法。"""
    expr = (cron_expr or "").strip()
    if len(expr.split()) != 5:
        raise ValueError("cron_expr 需为 5 字段（分 时 日 月 周），如「0 9 * * *」每天 9 点")
    try:
        croniter(expr, datetime.now(timezone.utc))
    except (ValueError, KeyError) as e:
        raise ValueError(f"cron 表达式无效：{e}")


def compute_next_run(cron_expr: str, after: datetime | None = None) -> datetime:
    """计算 cron 表达式在 after 之后的最近一次触发时间（UTC，向后取整到下一分钟）。"""
    base = after or datetime.now(timezone.utc)
    it = croniter(cron_expr, base)
    return it.get_next(datetime)


# ─── CRUD ─────────────────────────────────────────────

def list_schedules(db: Session) -> list[AgentSchedule]:
    return list(db.scalars(select(AgentSchedule).order_by(AgentSchedule.created_at.desc())).all())


def get_schedule(db: Session, schedule_id: UUID) -> AgentSchedule | None:
    return db.get(AgentSchedule, schedule_id)


def create_schedule(db: Session, payload) -> AgentSchedule:
    validate_cron(payload.cron_expr)
    if payload.action_type not in ACTION_TYPES:
        raise ValueError(f"action_type 仅支持：{', '.join(sorted(ACTION_TYPES))}")
    if payload.action_type == "prompt" and not (payload.prompt or "").strip():
        raise ValueError("prompt 类任务必须填写 prompt（交给 LLM 的生成指令）")
    if payload.action_type == "system" and payload.system_action not in SYSTEM_ACTIONS:
        raise ValueError(f"system 类任务动作仅支持：{', '.join(sorted(SYSTEM_ACTIONS))}")
    row = AgentSchedule(
        name=(payload.name or "").strip(),
        cron_expr=payload.cron_expr.strip(),
        action_type=payload.action_type,
        prompt=(payload.prompt or "").strip(),
        system_action=payload.system_action or "",
        session_id=payload.session_id,
        enabled=payload.enabled,
        next_run_at=compute_next_run(payload.cron_expr.strip()),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_schedule(db: Session, schedule_id: UUID, payload) -> AgentSchedule:
    row = db.get(AgentSchedule, schedule_id)
    if not row:
        return None
    data = payload.model_dump(exclude_unset=True)
    if "cron_expr" in data and data["cron_expr"]:
        validate_cron(data["cron_expr"])
        row.cron_expr = data["cron_expr"].strip()
        # 表达式变化 → 从当前时间起重算下次运行
        row.next_run_at = compute_next_run(row.cron_expr)
    if "action_type" in data:
        if data["action_type"] not in ACTION_TYPES:
            raise ValueError(f"action_type 仅支持：{', '.join(sorted(ACTION_TYPES))}")
        row.action_type = data["action_type"]
    for field in ("name", "prompt", "system_action", "session_id", "enabled"):
        if field in data:
            setattr(row, field, data[field])
    db.commit()
    db.refresh(row)
    return row


def delete_schedule(db: Session, schedule_id: UUID) -> bool:
    row = db.get(AgentSchedule, schedule_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


# ─── 执行 ─────────────────────────────────────────────

def run_schedule(db: Session, schedule_id: UUID) -> str:
    """立即执行一次定时任务（API「立即运行」与 beat tick 共用）。返回执行结果文本。"""
    row = db.get(AgentSchedule, schedule_id)
    if not row:
        raise ValueError("定时任务不存在")
    try:
        if row.action_type == "prompt":
            result = _run_prompt_action(db, row)
        elif row.action_type == "system":
            result = _run_system_action(db, row)
        else:
            result = f"未知 action_type：{row.action_type}"
        row.last_run_at = datetime.now(timezone.utc)
        row.run_count = (row.run_count or 0) + 1
        row.last_error = None
    except Exception as e:  # noqa: BLE001 - 定时任务失败不中断调度
        logger.warning("定时任务 %s 执行失败: %s", row.name, e)
        row.last_run_at = datetime.now(timezone.utc)
        row.last_error = str(e)[:500]
        result = f"执行失败：{e}"
    # 无论成败都推进到下次触发，避免失败后每分钟重试
    row.next_run_at = compute_next_run(row.cron_expr, datetime.now(timezone.utc))
    db.commit()
    return result


def _run_prompt_action(db: Session, row: AgentSchedule) -> str:
    """prompt 类：调 LLM 生成内容，写入指定会话（未指定则自动新建）。"""
    from app.providers.registry import ProviderRegistry
    from app.services import agent_service

    model = agent_service._resolve_chat_model(db, None)
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat([
        {
            "role": "system",
            "content": (
                "你是一个定时自动化任务的执行者。根据任务指令生成内容，"
                "直接输出最终成果。使用简体中文，控制在 1500 字以内。"
            ),
        },
        {"role": "user", "content": row.prompt or "（无任务指令）"},
    ])
    # 推理模型（如 agnes-2.0-flash）非流式响应可能把内容放在 reasoning_content：
    # content 为空时回退到 reasoning_content
    msg = ((resp.get("choices") or [{}])[0].get("message") or {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("reasoning_content") or "").strip()
    if not content:
        raise ValueError("模型未返回有效内容")

    # 写入目标会话：无会话则自动新建（会话标题 = 定时任务名）
    session_id = row.session_id
    if session_id:
        session = db.get(AgentSession, session_id)
        if not session:
            raise ValueError(f"目标会话不存在：{session_id}")
    else:
        session = agent_service.create_session(db, title=f"[定时] {row.name}")
        row.session_id = session.id
        session_id = session.id
    db.add(AgentMessage(
        session_id=session_id,
        role="assistant",
        content=f"【定时任务 · {row.name}】\n\n{content}",
    ))
    db.commit()
    return f"已生成并写入会话（{len(content)} 字）"


def _run_system_action(db: Session, row: AgentSchedule) -> str:
    """system 类：执行系统动作。"""
    if row.system_action == "retry_failed_tasks":
        count, msg = _retry_all_failed_tasks(db)
        return f"{msg}（重试 {count} 个）" if count else msg
    return f"未知系统动作：{row.system_action}"


def _retry_all_failed_tasks(db: Session) -> tuple[int, str]:
    """批量重跑全部失败任务：重置为 pending 并重新派发对应 celery 任务。

    与任务中心 retry_failed 逻辑一致，但跨项目扫描（定时全局动作）。
    """
    from app.tasks.celery_app import celery_app

    failed = list(db.scalars(
        select(Task).where(Task.status == TaskStatus.failed)
    ).all())
    if not failed:
        return 0, "无失败任务可重跑"
    # 2026-08-15 与 task_service.retry_failed 对齐（H1 副本）：先备份 payload 型任务配置，
    # canvas_generate 的 provider_task_id 是画布节点配置（任务端读取），重跑必须保留；
    # export_film 的 provider_task_id 是导出选项 JSON（任务端不读，重跑经 kwargs 恢复首次选项）。
    saved_payloads: dict[str, dict] = {}
    for t in failed:
        if t.type in (TaskType.export_film, TaskType.canvas_generate):
            try:
                cfg = json.loads(t.provider_task_id or "{}")
                if isinstance(cfg, dict) and cfg:
                    saved_payloads[str(t.id)] = cfg
            except (ValueError, TypeError):
                logger.warning("定时重试任务 payload 解析失败 task_id=%s type=%s", t.id, t.type)
    retried = []
    for t in failed:
        t.status = TaskStatus.pending
        t.error = None
        t.progress = 0
        if t.type != TaskType.canvas_generate:
            t.provider_task_id = None
        t.poll_url = None
        t.started_at = None
        t.finished_at = None
        t.last_heartbeat_at = None
        retried.append(t)
    db.commit()
    dispatched = 0
    for t in retried:
        try:
            args: list = [str(t.id)]
            if t.type.value in ("generate_bgm", "generate_sfx"):
                args.append(str(t.project_id) if t.project_id else "")
            kwargs: dict = {}
            if t.type == TaskType.export_film:
                kwargs = {
                    k: bool(v) for k, v in (saved_payloads.get(str(t.id)) or {}).items()
                }
            celery_app.send_task(t.type.value, args=args, kwargs=kwargs)
            dispatched += 1
        except Exception as e:  # noqa: BLE001 - 单任务派发失败不阻断
            logger.warning("定时重试任务派发失败 task_id=%s: %s", t.id, e)
    return dispatched, f"已重跑 {len(retried)} 个失败任务"
