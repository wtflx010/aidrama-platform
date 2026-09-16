"""批量配音业务服务：整幕/整项目配音批量重跑。

批量重跑逻辑：
1. 收集 episode/project 下所有 segment
2. 删除每个 segment 既有 VoiceLine（cascade 删 Task）
3. 按 voice_service.generate 重新生成（多条 VoiceLine + Task）
4. 建父 batch_voice Task 汇总进度（复用 batch 编排模式）
"""
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.models.voice import VoiceLine
from app.schemas.batch import BatchBody
from app.schemas.voice import VoiceGenerate
from app.services import voice_service


def _collect_episode_segments(db: Session, episode_id):
    """收集该幕下未锁定的分镜（尊重分镜锁定），按 index 升序。"""
    return db.scalars(
        select(Segment).where(Segment.episode_id == episode_id, Segment.locked.is_(False))
        .order_by(Segment.index.asc())
    ).all()


def _collect_project_segments(db: Session, project_id):
    """收集项目下未锁定的分镜（尊重分镜锁定）。"""
    from app.models.project import Episode
    return db.scalars(
        select(Segment)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(Episode.project_id == project_id, Segment.locked.is_(False))
        .order_by(Episode.index.asc(), Segment.index.asc())
    ).all()


def _delete_existing_voicelines(db: Session, segment_ids: list) -> int:
    """删除 segments 的既有 VoiceLine（关联 Task 由 cascade 处理）。返回删除条数。"""
    if not segment_ids:
        return 0
    result = db.execute(
        delete(VoiceLine).where(VoiceLine.segment_id.in_(segment_ids))
    )
    return result.rowcount or 0


def batch_regenerate_episode(db: Session, episode_id, payload: BatchBody | None = None):
    """整幕配音批量重跑。"""
    from app.tasks.batch_voice import batch_voice as batch_task

    segments = _collect_episode_segments(db, episode_id)
    if not segments:
        raise ValueError("该幕下无未锁定分镜，无需批量重跑")

    project_id = segments[0].episode.project_id
    seg_ids = [s.id for s in segments]
    # 删除既有 VoiceLine
    _delete_existing_voicelines(db, seg_ids)
    db.flush()

    # 重新生成：收集所有子任务 ID
    sub_task_ids: list[str] = []
    model_id = payload.model_id if payload else None
    vp = VoiceGenerate(model_id=model_id) if model_id else None
    for seg in segments:
        try:
            results = voice_service.generate(db, seg.id, vp)
            for _, t in results:
                sub_task_ids.append(str(t.id))
        except ValueError:
            # 该镜无对白/旁白，跳过
            continue

    if not sub_task_ids:
        raise ValueError("该幕下无分镜可生成配音（均无对白/旁白）")

    task = Task(
        project_id=project_id, type=TaskType.batch_voice,
        target_type="episode", target_id=episode_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(sub_task_ids),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    batch_task.delay(str(task.id))
    return task, len(sub_task_ids), sub_task_ids


def batch_regenerate_project(db: Session, project_id, payload: BatchBody | None = None):
    """整项目配音批量重跑（跨幕）。"""
    from app.tasks.batch_voice import batch_voice as batch_task

    segments = _collect_project_segments(db, project_id)
    if not segments:
        raise ValueError("该项目下无未锁定分镜，无需批量重跑")

    seg_ids = [s.id for s in segments]
    _delete_existing_voicelines(db, seg_ids)
    db.flush()

    sub_task_ids: list[str] = []
    model_id = payload.model_id if payload else None
    vp = VoiceGenerate(model_id=model_id) if model_id else None
    for seg in segments:
        try:
            results = voice_service.generate(db, seg.id, vp)
            for _, t in results:
                sub_task_ids.append(str(t.id))
        except ValueError:
            continue

    if not sub_task_ids:
        raise ValueError("该项目下无分镜可生成配音（均无对白/旁白）")

    task = Task(
        project_id=project_id, type=TaskType.batch_voice,
        target_type="project", target_id=project_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(sub_task_ids),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    batch_task.delay(str(task.id))
    return task, len(sub_task_ids), sub_task_ids
