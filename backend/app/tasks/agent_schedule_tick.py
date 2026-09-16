"""定时自动化任务调度（P8）：扫描到期的 agent_schedule 并执行。

由 celery beat 每 30s 触发一次。执行策略：
- 查询 enabled 且 next_run_at <= now 的任务
- 逐个调用 agent_schedule_service.run_schedule（内部更新 last_run_at/next_run_at，
  失败记录 last_error 后仍推进到下次触发，避免失败后每分钟重试）
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models.agent import AgentSchedule
from app.services import agent_schedule_service
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="agent_schedule_tick")
def agent_schedule_tick():
    """扫描到期定时任务并执行，返回本次执行数量。"""
    db = SessionLocal()
    executed = 0
    try:
        now = datetime.now(timezone.utc)
        due = list(db.scalars(
            select(AgentSchedule).where(
                AgentSchedule.enabled.is_(True),
                AgentSchedule.next_run_at.is_not(None),
                AgentSchedule.next_run_at <= now,
            )
        ).all())
        for row in due:
            try:
                msg = agent_schedule_service.run_schedule(db, row.id)
                logger.info("[schedule] 执行「%s」: %s", row.name, msg)
                executed += 1
            except Exception as e:  # noqa: BLE001 - 单任务失败不阻断其余
                logger.warning("[schedule] 执行「%s」异常: %s", row.name, e)
    finally:
        db.close()
    return executed
