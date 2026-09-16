"""SFX 音效生成 Celery 任务。"""
from app.database import SessionLocal
from app.models.task import Task, TaskStatus
from app.providers.errors import map_to_chinese
from app.tasks.base import heartbeat_guard, now, update_task
from app.tasks.celery_app import celery_app


@celery_app.task(name="generate_sfx", bind=True)
def generate_sfx_task(self, task_id: str, project_id: str, segment_ids: list[str] | None = None,
                      model_id: str | None = None):
    """为项目（或指定分镜）生成音效。

    流程：LLM 标注 → Freesound 检索下载 → 写 SfxClip。
    """
    from app.services import sfx_service

    db = SessionLocal()
    try:
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        with heartbeat_guard(task_id, interval=15):
            clips = sfx_service.generate_sfx_clips(
                db, project_id,
                segment_ids=segment_ids,
                model_id=model_id,
            )
        succeeded = sum(1 for c in clips if c.status == "done")
        failed = sum(1 for c in clips if c.status == "failed")
        if succeeded == 0 and failed > 0:
            err = clips[0].error if clips else "未知错误"
            raise RuntimeError(f"全部 {failed} 个音效生成失败：{err}")
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/projects/{project_id}/sfx",
            finished_at=now(),
        )
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()
