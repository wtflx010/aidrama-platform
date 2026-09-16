"""批量配音生成编排任务：轮询子任务进度，汇总成功/失败。

复用 batch_keyframes 的编排模式。
"""
import json
import time

from app.database import SessionLocal
from app.models.task import Task, TaskStatus
from app.providers.errors import map_to_chinese
from app.tasks.base import now, update_task
from app.tasks.celery_app import celery_app

BATCH_POLL_INTERVAL = 3  # 秒
BATCH_TIMEOUT = 1800  # 30 分钟（配音生成较慢，给足时间）


@celery_app.task(name="batch_voice", bind=True)
def batch_voice(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        sub_ids: list[str] = json.loads(task.provider_task_id or "[]")
        if not sub_ids:
            update_task(db, task_id, status=TaskStatus.failed, error="无子任务", finished_at=now())
            return

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=0)

        start = time.time()
        timed_out = False
        while time.time() - start < BATCH_TIMEOUT:
            # 父任务被用户取消/项目删除 → 立即退出，不覆盖 cancelled
            cur = db.get(Task, task_id)
            if cur is None or cur.status == TaskStatus.cancelled:
                return
            succeeded = failed = running = 0
            for sid in sub_ids:
                st = db.get(Task, sid)
                if st is None:
                    failed += 1
                elif st.status == TaskStatus.succeeded:
                    succeeded += 1
                elif st.status == TaskStatus.failed:
                    failed += 1
                else:
                    running += 1
            progress = int((succeeded + failed) / len(sub_ids) * 100)
            update_task(db, task_id, progress=progress, last_heartbeat_at=now())
            if running == 0:
                break
            time.sleep(BATCH_POLL_INTERVAL)
        else:
            # 超时退出（while 条件不满足）：仍有子任务在跑
            timed_out = True

        # 循环退出后可能刚被取消：终态不得覆盖 cancelled
        if (db.get(Task, task_id) or Task(status=TaskStatus.failed)).status == TaskStatus.cancelled:
            return

        # 超时且仍有子任务在跑 → 标记 failed，不误判为成功
        if timed_out:
            still_running = sum(
                1 for sid in sub_ids
                if (db.get(Task, sid) or Task(status=TaskStatus.failed)).status
                in (TaskStatus.pending, TaskStatus.running)
            )
            if still_running > 0:
                update_task(
                    db, task_id, status=TaskStatus.failed,
                    error=f"批量任务超时，{still_running}/{len(sub_ids)} 个子任务仍在执行",
                    finished_at=now(),
                )
                return

        failed_ids = [sid for sid in sub_ids if (db.get(Task, sid) or Task(status=TaskStatus.failed)).status == TaskStatus.failed]
        if failed_ids:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error=f"{len(failed_ids)}/{len(sub_ids)} 条配音失败：{failed_ids[:5]}",
                finished_at=now(),
            )
        else:
            update_task(db, task_id, status=TaskStatus.succeeded, progress=100, finished_at=now())
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()
