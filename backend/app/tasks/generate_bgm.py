"""BGM 生成 Celery 任务。

异步触发，调用 MusicGen API 生成各幕 BGM 并写入 BgmTrack 表。
"""
from app.database import SessionLocal
from app.models.task import Task, TaskStatus
from app.providers.errors import map_to_chinese
from app.tasks.base import heartbeat_guard, now, update_task
from app.tasks.celery_app import celery_app


@celery_app.task(name="generate_bgm", bind=True)
def generate_bgm_task(self, task_id: str, project_id: str, episode_id: str | None = None):
    """为项目（或单幕）生成 BGM。

    流程：分析情感需求 → 调 MusicGen 生成 → 写 BgmTrack。
    """
    from app.services import bgm_service

    db = SessionLocal()
    try:
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        with heartbeat_guard(task_id, interval=15):
            tracks = bgm_service.generate_project_bgm(db, project_id, episode_id=episode_id)
        succeeded = sum(1 for t in tracks if t.status == "done")
        failed = sum(1 for t in tracks if t.status == "failed")
        if succeeded == 0 and failed > 0:
            raise RuntimeError(f"全部 {failed} 个 BGM 生成失败：{tracks[0].error if tracks else '未知错误'}")
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/projects/{project_id}/bgm",
            finished_at=now(),
        )
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()
