"""幕业务服务。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Episode
from app.schemas.episode import EpisodeCreate, EpisodeUpdate


def list_by_project(db: Session, project_id):
    return db.scalars(
        select(Episode)
        .where(Episode.project_id == project_id)
        .order_by(Episode.index.asc())
    ).all()


def get(db: Session, episode_id) -> Episode | None:
    return db.get(Episode, episode_id)


def create(db: Session, project_id, payload: EpisodeCreate) -> Episode:
    idx = payload.index
    if idx is None:
        cur = db.scalar(select(func.max(Episode.index)).where(Episode.project_id == project_id))
        # 注意：不能用 `cur or -1`，因为 0 是 falsy 会导致 index 重复
        idx = (cur if cur is not None else -1) + 1
    ep = Episode(
        project_id=project_id, index=idx,
        title=payload.title, synopsis=payload.synopsis,
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


def update(db: Session, episode_id, payload: EpisodeUpdate) -> Episode | None:
    ep = db.get(Episode, episode_id)
    if not ep:
        return None
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(ep, k, v)
    db.commit()
    db.refresh(ep)
    return ep


def delete(db: Session, episode_id) -> bool:
    ep = db.get(Episode, episode_id)
    if not ep:
        return False
    db.delete(ep)
    db.commit()
    return True


def reorder(db: Session, project_id, ordered_ids: list) -> list[Episode]:
    for i, eid in enumerate(ordered_ids):
        ep = db.get(Episode, eid)
        if ep and ep.project_id == project_id:
            ep.index = i
    db.commit()
    return list_by_project(db, project_id)
