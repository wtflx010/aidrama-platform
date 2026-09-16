"""批量视频生成编排任务：轮询子任务进度，汇总成功/失败。"""
import json
import time
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models.media import Keyframe, MediaStatus
from app.models.task import Task, TaskStatus
from app.providers.errors import map_to_chinese
from app.schemas.video import VideoGenerate
from app.services import video_service
from app.tasks.base import now, update_task
from app.tasks.celery_app import celery_app

BATCH_POLL_INTERVAL = 5  # 视频生成较慢，轮询间隔加大
BATCH_TIMEOUT = 3600  # 60 分钟


@celery_app.task(name="batch_videos", bind=True)
def batch_videos(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        try:
            cfg = json.loads(task.provider_task_id or "[]")
        except (json.JSONDecodeError, TypeError):
            cfg = []

        # ——— 链式串行模式（2026-09）———
        # 触发：批量内存在 prev_tail 分镜（或显式 chained=true）。逐镜顺序出片：
        # 上一镜 succeeded（+video_url 落库）后才派发下一镜，prev_tail 分支据此抽出
        # 上一镜真实尾帧 PNG 作为本镜首帧，实现"多分镜一起生成也逐镜续帧"。
        if isinstance(cfg, dict) and cfg.get("chained"):
            seg_ids = cfg.get("segments") or []
            model_id = (cfg.get("model_id") or "").strip()
            if not seg_ids:
                update_task(db, task_id, status=TaskStatus.failed, error="链式批量无分镜", finished_at=now())
                return
            update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=0)
            start = time.time()
            total = len(seg_ids)
            done = 0
            failed_reason: str | None = None
            for idx, seg_uuid in enumerate(seg_ids):
                if time.time() - start > BATCH_TIMEOUT:
                    failed_reason = "批量超时"
                    break
                cur = db.get(Task, task_id)
                if cur is None or cur.status == TaskStatus.cancelled:
                    return
                seg_id = uuid.UUID(str(seg_uuid))
                # 本镜关键帧（有则作首帧，无则走 pEv 资产/prev_tail 链路）
                kf = db.scalar(
                    select(Keyframe).where(
                        Keyframe.segment_id == seg_id,
                        Keyframe.status == MediaStatus.succeeded,
                    ).order_by(Keyframe.created_at.desc())
                )
                vid_payload = VideoGenerate(
                    keyframe_id=(kf.id if kf and kf.image_url else None),
                    model_id=(uuid.UUID(model_id) if model_id else None),
                )
                try:
                    _clip, sub_task = video_service.generate(db, seg_id, vid_payload)
                except Exception as e:  # noqa: BLE001
                    failed_reason = str(e)[:200]
                    break
                sub_start = time.time()
                while time.time() - sub_start < 1800:
                    if time.time() - start > BATCH_TIMEOUT:
                        failed_reason = "批量超时"
                        break
                    t = db.get(Task, sub_task.id)
                    if t is None:
                        failed_reason = "子任务丢失"
                        break
                    if t.status == TaskStatus.succeeded:
                        done += 1
                        break
                    if t.status in (TaskStatus.failed, TaskStatus.cancelled):
                        failed_reason = (t.error or "生成失败")[:200]
                        # failed 不计入 done；中止链
                        break
                    # 串行等待上一镜渲染期间每轮刷新父任务心跳，避免 reclaim（5min 无心跳）
                    # 看门狗把链式批量父任务误判卡死回收（2026-09-01 事故修复）
                    update_task(db, task_id, progress=int(done / total * 100), last_heartbeat_at=now())
                    time.sleep(BATCH_POLL_INTERVAL)
                update_task(db, task_id, progress=int(done / total * 100), last_heartbeat_at=now())
                if failed_reason:
                    break
            if failed_reason:
                update_task(
                    db, task_id, status=TaskStatus.failed,
                    error=f"链式批量中止于第 {done + 1}/{total} 镜：{failed_reason}",
                    finished_at=now(),
                )
            else:
                update_task(db, task_id, status=TaskStatus.succeeded, progress=100, finished_at=now())
            return

        # ——— 原有并发聚合模式 ———
        sub_ids: list[str] = cfg if isinstance(cfg, list) else []
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
                error=f"{len(failed_ids)}/{len(sub_ids)} 个视频失败：{failed_ids[:5]}",
                finished_at=now(),
            )
        else:
            update_task(db, task_id, status=TaskStatus.succeeded, progress=100, finished_at=now())
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()
