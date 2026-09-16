"""成片评估任务（P0-1）：规则分 + LLM 四维分，写入 EpisodeEval。

对标 Higgsfield Virality Predictor：对一集成片打「钩子/注意力/留存/病毒性」
四维分并产出逐分镜反哺建议。纯文本 LLM 任务（无 provider 轮询），
用 heartbeat_guard 保持心跳。
"""
import logging
import uuid

from app.database import SessionLocal
from app.models.episode_eval import EpisodeEval
from app.models.project import Episode
from app.models.task import Task, TaskStatus
from app.services import evaluate_service
from app.tasks.base import heartbeat_guard, now, update_task
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="evaluate_episode", bind=True)
def evaluate_episode(self, task_id: str, episode_id: str):
    db = SessionLocal()
    row: EpisodeEval | None = None
    try:
        task = db.get(Task, task_id)
        if task is None:
            db.close()
            return
        try:
            ep_uuid = uuid.UUID(str(episode_id))
        except (ValueError, TypeError):
            update_task(db, task_id, status=TaskStatus.failed, error="幕 ID 非法", finished_at=now())
            db.close()
            return
        episode = db.get(Episode, ep_uuid)
        if episode is None:
            update_task(db, task_id, status=TaskStatus.failed, error="幕不存在", finished_at=now())
            db.close()
            return

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        row = evaluate_service.new_eval_row(db, ep_uuid)
        task.target_id = ep_uuid
        db.commit()

        with heartbeat_guard(task_id):
            result = evaluate_service.evaluate_episode(db, episode)
            evaluate_service.save_result(db, row, result)

        update_task(db, task_id, status=TaskStatus.succeeded, progress=100,
                    finished_at=now(), result_url=result.get("video_url"))
        db.commit()
        logger.info("[evaluate] 幕 %s 评估完成: %s", episode_id, result["scores"])
    except Exception as e:  # noqa: BLE001
        db.rollback()
        msg = str(e)[:500]
        if row is not None:
            try:
                evaluate_service.fail(db, row, msg)
            except Exception:  # noqa: BLE001
                db.rollback()
        try:
            update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        except Exception:  # noqa: BLE001
            pass
        db.commit()
        logger.exception("[evaluate] 幕 %s 评估失败", episode_id)
    finally:
        db.close()
