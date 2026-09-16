"""批量生成业务服务：整幕关键帧/视频批量生成，尊重分镜锁定。"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import Keyframe, MediaStatus, VideoClip
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.batch import BatchBody
from app.schemas.keyframe import KeyframeGenerate
from app.services import keyframe_service, video_service

logger = logging.getLogger(__name__)


def _collect_unlocked_segments(db: Session, episode_id):
    """收集该幕下未锁定的分镜，按 index 升序。"""
    return db.scalars(
        select(Segment).where(Segment.episode_id == episode_id, Segment.locked.is_(False))
        .order_by(Segment.index.asc())
    ).all()


def batch_keyframes(db: Session, episode_id, payload: BatchBody):
    """批量生成关键帧：为每个未锁定分镜派发 generate_keyframe 子任务。"""
    from app.tasks.batch_keyframes import batch_keyframes as batch_task

    segments = _collect_unlocked_segments(db, episode_id)
    if not segments:
        raise ValueError("该幕下无未锁定分镜，无需批量生成")

    project_id = segments[0].episode.project_id
    # 派发子任务（复用 keyframe_service.generate 建 Keyframe+Task 行 + .delay()）
    # 批量重跑前先删各分镜旧关键帧，保证每分镜关键帧唯一（避免历史版本堆积）
    sub_task_ids: list[str] = []
    for seg in segments:
        keyframe_service.delete_segment_keyframes(db, seg.id)
        kf_payload = KeyframeGenerate(prompt=seg.description or f"分镜{seg.index}", model_id=payload.model_id)
        _, sub_task = keyframe_service.generate(db, seg.id, kf_payload)
        sub_task_ids.append(str(sub_task.id))

    # 建批量编排任务（父任务本身不计费，子任务各自记账）
    task = Task(
        project_id=project_id, type=TaskType.batch_keyframes,
        target_type="episode", target_id=episode_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(sub_task_ids),  # 存子任务 ID 防丢失
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    batch_task.delay(str(task.id))
    return task, len(segments), sub_task_ids


def _apply_batch_ref_source(segments, ref_src: str, custom_first_frame_url: str | None) -> bool:
    """批量「初始帧来源」写入参与分镜的 gen_params，返回是否任一为 prev_tail。

    2026-09 修复「来源不生效」：选「无」(none) 必须重置 reference_src，
    否则上一批遗留的 prev_tail/custom 会残留在 gen_params，误触发链式串行
    或首帧覆盖，导致后续批次的「初始帧来源」看似不生效。
    """
    any_prev_tail = False
    for s in segments:
        gp = dict(s.gen_params or {})
        if ref_src == "custom" and custom_first_frame_url:
            gp["reference_src"] = "custom"
            gp["custom_first_frame_url"] = custom_first_frame_url
        elif ref_src == "prev_tail":
            gp["reference_src"] = "prev_tail"
            # 手工上传等其他模式源清理（避免跨模式残留自定义首帧）
            gp.pop("custom_first_frame_url", None)
            any_prev_tail = True
        elif ref_src == "none":
            # 选「无」必须重置 reference_src，清除上一批遗留的 prev_tail/custom
            gp["reference_src"] = "none"
            gp.pop("custom_first_frame_url", None)
        s.gen_params = gp
    return any_prev_tail


def batch_videos(db: Session, episode_id, payload: BatchBody):
    """批量生成视频：
    - 默认（并发）：为每个分镜立即派发 generate_video 子任务，父任务只汇总；
    - 链式串行（2026-09-01）：触发条件 = payload.chained=True，或（自动）批量内存在
      勾选「上一分镜尾帧」(gen_params.reference_src=prev_tail) 的分镜时自动串行——
      逐镜顺序出片，上一镜 succeeded 后才派下一镜，首帧取上一镜真实尾帧。
    """
    from app.tasks.batch_videos import batch_videos as batch_task

    segments = _collect_unlocked_segments(db, episode_id)
    if not segments:
        raise ValueError("该幕下无未锁定分镜，无需批量生成")

    # ── 2026-09-02 批量「覆盖已生成视频」：未勾选则跳过已有成功成片的分镜 ──
    if not payload.overwrite:
        segments = [
            s for s in segments
            if db.query(VideoClip).filter(
                VideoClip.segment_id == s.id,
                VideoClip.status == MediaStatus.succeeded,
            ).first() is None
        ]
        if not segments:
            raise ValueError("所选分镜均已有成片（未勾选「覆盖已生成视频」），无需重新生成")

    # ── 批量「初始帧来源」应用到参与分镜的 gen_params ──
    ref_src = (payload.reference_src or "").strip()
    any_prev_tail = False
    if ref_src in ("custom", "prev_tail", "none"):
        any_prev_tail = _apply_batch_ref_source(segments, ref_src, payload.custom_first_frame_url)
        for s in segments:
            db.add(s)
        db.commit()

    project_id = segments[0].episode.project_id
    # ── 链式触发判定（prev_tail 模式强制串行）──
    chained = bool(payload.chained) or (payload.chained is None and any_prev_tail)
    if chained:
        parent = Task(
            project_id=project_id, type=TaskType.batch_videos,
            target_type="episode", target_id=episode_id,
            status=TaskStatus.pending,
            provider_task_id=json.dumps({
                "chained": True,
                "segments": [str(s.id) for s in segments],
                "model_id": str(payload.model_id) if payload.model_id else "",
            }),
        )
        db.add(parent)
        db.commit()
        db.refresh(parent)
        batch_task.delay(str(parent.id))
        return parent, len(segments), [str(parent.id)]

    sub_task_ids: list[str] = []
    skipped: list[str] = []
    from app.schemas.video import VideoGenerate
    for seg in segments:
        # 批量重跑前先删该分镜旧视频，保证每分镜视频唯一
        n_old = video_service.delete_segment_clips(db, seg.id)
        if n_old:
            logger.info("[batch] 批量视频前删除分镜 %s 旧视频 %d 条", seg.id, n_old)
        # 2026-08-09：不再强制关键帧——无关键帧时走「资产参考图 + 提示词」链路
        # （R2V 参考图在 generate_video 任务内按 segment 场景/角色/道具资产解析）。
        # 有成功关键帧时仍以其为首帧（旧链路兼容）。
        kf = db.scalar(
            select(Keyframe).where(
                Keyframe.segment_id == seg.id, Keyframe.status == MediaStatus.succeeded
            ).order_by(Keyframe.created_at.desc())
        )
        vid_payload = VideoGenerate(
            keyframe_id=(kf.id if kf and kf.image_url else None),
            model_id=payload.model_id,
        )
        _, sub_task = video_service.generate(db, seg.id, vid_payload)
        sub_task_ids.append(str(sub_task.id))

    if not sub_task_ids:
        raise ValueError("没有可生成视频的分镜（需先完成关键帧生成）")

    task = Task(
        project_id=project_id, type=TaskType.batch_videos,
        target_type="episode", target_id=episode_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(sub_task_ids),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    batch_task.delay(str(task.id))
    return task, len(sub_task_ids), sub_task_ids


def batch_project_videos(db: Session, project_id, payload: BatchBody = None):
    # 2026-08-22 三列工作台「生成所有分镜」：遍历项目所有幕，逐幕 batch_videos 派发子任务。
    from app.models.project import Episode
    if payload is None:
        payload = BatchBody()
    eps = db.scalars(select(Episode).where(Episode.project_id == project_id).order_by(Episode.index)).all()
    if not eps:
        raise ValueError('项目下没有幕')
    all_sub_ids = []
    for ep in eps:
        try:
            _, total, sub_ids = batch_videos(db, ep.id, payload)
            all_sub_ids.extend(sub_ids)
        except ValueError:
            continue
    if not all_sub_ids:
        raise ValueError('项目下没有可生成视频的分镜')
    from app.tasks.batch_videos import batch_videos as batch_task
    parent = Task(
        project_id=project_id, type=TaskType.batch_videos,
        target_type='project', target_id=project_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(all_sub_ids),
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)
    batch_task.delay(str(parent.id))
    return parent, len(all_sub_ids), all_sub_ids


def batch_project_upscales(db: Session, project_id, payload: BatchBody = None, tier: str = None):
    """一键超分所有分镜：遍历项目分镜，为每个「有成功 480p 母片且未超分」的分镜派发超分任务。

    tier 缺省取项目 video_params.upscale_tier（默认 4x）。父任务复用 batch_videos
    celery 任务做编排（轮询子任务汇总成败），task.type=batch_upscale_videos。
    """
    from app.models.project import Episode, Project
    from app.services.upscale_service import find_source_clip, generate, has_upscaled

    eps = db.scalars(select(Episode).where(Episode.project_id == project_id).order_by(Episode.index)).all()
    if not eps:
        raise ValueError('项目下没有幕')
    project = db.get(Project, eps[0].project_id)
    pvp = (project.video_params or {}) if (project and project.video_params) else {}
    if not tier:
        try:
            tier = str(pvp.get('upscale_tier') or '4x')
        except Exception:
            tier = '4x'
    sub_ids: list[str] = []
    for ep in eps:
        segs = db.scalars(select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)).all()
        for s in segs:
            src = find_source_clip(db, s.id)
            if src is None:
                continue
            if has_upscaled(db, src.id):
                continue
            try:
                _, sub = generate(db, src, tier=tier)
                sub_ids.append(str(sub.id))
            except ValueError:
                continue
    if not sub_ids:
        raise ValueError('项目下没有可超分的分镜（需先完成分镜视频生成）')
    from app.tasks.batch_videos import batch_videos as batch_task
    parent = Task(
        project_id=project_id, type=TaskType.batch_upscale_videos,
        target_type='project', target_id=project_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(sub_ids),
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)
    batch_task.delay(str(parent.id))
    return parent, len(sub_ids), sub_ids


def batch_asset_covers(db: Session, project_id, asset_type=None, payload: BatchBody = None):
    """批量生成资产封面：为项目下指定类型、尚未生成封面（cover_url 为空）的资产
    逐个派发 generate_asset_cover 子任务（复用单资产生成逻辑与计费）。

    跳过已有封面的资产（不覆盖已生成成果），避免误覆盖。
    """
    from app.models.asset import Asset, AssetType
    from app.services import asset_service

    stmt = select(Asset).where(Asset.project_id == project_id)
    if asset_type is not None:
        stmt = stmt.where(Asset.type == asset_type)
    assets = db.scalars(stmt.order_by(Asset.created_at.asc())).all()

    # 只挑无封面的资产
    targets = [a for a in assets if not a.cover_url]
    if not targets:
        raise ValueError("没有待生成封面的资产（本类型资产已全部有封面）")

    sub_task_ids: list[str] = []
    model_id = (payload.model_id if payload else None)
    for a in targets:
        _, sub_task = asset_service.generate_cover(db, a.id, model_id=model_id)
        sub_task_ids.append(str(sub_task.id))

    # 建批量编排任务（父任务本身不计费，子任务各自记账）
    task = Task(
        project_id=project_id, type=TaskType.batch_asset_covers,
        target_type=asset_type.value if asset_type else "asset",
        target_id=project_id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps(sub_task_ids),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from app.tasks.batch_asset_covers import batch_asset_covers as batch_task
    batch_task.delay(str(task.id))
    return task, len(sub_task_ids), sub_task_ids, [str(a.id) for a in targets]