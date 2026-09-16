"""分镜业务服务。"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.segment import Segment
from app.schemas.segment import SegmentCreate, SegmentUpdate
from app.services.shot_beats import normalize_shot_beats


def list_by_episode(db: Session, episode_id) -> list[Segment]:
    q = select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.index.asc())
    return db.scalars(q).all()


def list_by_project(db: Session, project_id) -> list[Segment]:
    """按 episode.index、segment.index 顺序取项目下所有分镜。"""
    from app.models.project import Episode

    q = (
        select(Segment)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
        .order_by(Episode.index.asc(), Segment.index.asc())
    )
    return db.scalars(q).all()


def get(db: Session, segment_id) -> Segment | None:
    return db.get(Segment, segment_id)


def create(db: Session, episode_id, payload: SegmentCreate) -> Segment:
    idx = payload.index
    if idx is None:
        cur = db.scalar(
            select(func.max(Segment.index)).where(Segment.episode_id == episode_id)
        )
        # 注意：不能用 `cur or 0`，因为 0 是 falsy
        idx = (cur if cur is not None else 0) + 1
    s = Segment(
        episode_id=episode_id,
        index=idx,
        shot_type=payload.shot_type,
        camera=payload.camera,
        description=payload.description,
        dialogue=payload.dialogue,
        narration=payload.narration,
        duration=payload.duration,
        shot_beats=normalize_shot_beats(
            payload.shot_beats,
            payload.duration or 5.0,
            payload.shot_type,
            payload.camera,
        ),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def update(db: Session, segment_id, payload: SegmentUpdate) -> Segment | None:
    s = db.get(Segment, segment_id)
    if not s:
        return None
    # model_dump(mode="json") 把 UUID 等类型转为 JSON 兼容类型（str），
    # 避免 psycopg 往 JSONB/String 列写 UUID 时报 "not JSON serializable"
    updates = payload.model_dump(exclude_unset=True, mode="json")
    # 2026-08-28：shot_beats 落库前归一化——按目标时长连续铺满 0~duration、
    # 景别/运镜枚举校验回退；duration 可能同批更新，须用最新时长归一化。
    if "shot_beats" in updates:
        dur = updates.get("duration") if updates.get("duration") is not None else (s.duration or 5.0)
        updates["shot_beats"] = normalize_shot_beats(
            updates["shot_beats"], dur,
            updates.get("shot_type", s.shot_type),
            updates.get("camera", s.camera),
        )
    for k, v in updates.items():
        setattr(s, k, v)
    # 2026-08-09（H2 修复）：分镜内容/对白/资产编辑后必须清除增强 prompt 缓存，
    # 否则重新生成关键帧/视频时命中旧缓存、编辑静默不生效（clear_enhanced_prompt
    # 此前是死代码、全项目无 force=True 调用）。
    _clear_enhanced_cache(s)
    db.commit()
    db.refresh(s)
    return s


def _clear_enhanced_cache(s: Segment) -> None:
    """清除分镜增强缓存（描述/对白/资产变更后调用，保证下次生成重新增强）。"""
    if s.enhanced_prompt or s.enhanced_negative_prompt or s.enhanced_target:
        s.enhanced_prompt = None
        s.enhanced_negative_prompt = None
        s.enhanced_target = None


def clear_enhanced_prompt(db: Session, segment_id) -> None:
    """清除分镜增强缓存（外部入口，供资产绑定等调用）。"""
    s = db.get(Segment, segment_id)
    if s:
        _clear_enhanced_cache(s)
        db.commit()


def delete(db: Session, segment_id) -> bool:
    s = db.get(Segment, segment_id)
    if not s:
        return False
    # 清理关联媒体磁盘文件（关键帧图片 + 视频），DB 行由级联删除
    from app.utils.media import delete_media_file

    for kf in s.keyframes:
        delete_media_file(kf.image_url)
    for v in s.videos:
        delete_media_file(v.video_url)
    db.delete(s)
    db.commit()
    return True


def reorder(db: Session, episode_id, ordered_ids: list[UUID]) -> list[Segment]:
    for i, sid in enumerate(ordered_ids):
        s = db.get(Segment, sid)
        if s and s.episode_id == episode_id:
            s.index = i + 1
    db.commit()
    return list_by_episode(db, episode_id)
