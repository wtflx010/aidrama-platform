"""幕 API：创建/更新/删除/重排 + P7 幕级视频生成/查询。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Episode
from app.schemas.action_sequence import ActionSequenceOut
from app.schemas.episode import EpisodeCreate, EpisodeOut, EpisodeReorderBody, EpisodeUpdate
from app.schemas.episode_film import EpisodeFilmGenerate, EpisodeFilmGenerateOut
from app.schemas.episode_video import EpisodeVideoOut
from app.schemas.segment import SegmentOut
from app.schemas.task import TaskOut
from app.services import action_sequence_service, episode_service, episode_video_service
from app.services import project_longvideo_service

router = APIRouter()


@router.post("/projects/{project_id}/episodes", response_model=EpisodeOut, status_code=201)
def create_episode(project_id: UUID, payload: EpisodeCreate, db: Session = Depends(get_db)):
    return episode_service.create(db, project_id, payload)


@router.put("/episodes/{episode_id}", response_model=EpisodeOut)
def update_episode(episode_id: UUID, payload: EpisodeUpdate, db: Session = Depends(get_db)):
    ep = episode_service.update(db, episode_id, payload)
    if not ep:
        raise HTTPException(404, "幕不存在")
    return ep


@router.delete("/episodes/{episode_id}")
def delete_episode(episode_id: UUID, db: Session = Depends(get_db)):
    if not episode_service.delete(db, episode_id):
        raise HTTPException(404, "幕不存在")
    return {"ok": True}


@router.post("/projects/{project_id}/episodes/reorder", response_model=list[EpisodeOut])
def reorder_episodes(project_id: UUID, body: EpisodeReorderBody, db: Session = Depends(get_db)):
    return episode_service.reorder(db, project_id, body.ordered_ids)


# ─── 项目页签连续长片（2026-09，一集一条整片，不进画布）─────────────


@router.post("/episodes/{episode_id}/continuous-film", response_model=EpisodeFilmGenerateOut)
def generate_continuous_film(episode_id: UUID, body: EpisodeFilmGenerate | None = None,
                             db: Session = Depends(get_db)):
    """项目页签「一集一条连续长片」：把整集分镜串成一段连续整片（AIMixer 导演台，
    段间运动/音频续拍），产物回写 episode.continuous_film_*。不带画布。

    body 缺省=整集全部分镜；config 可带 task_type/ratio/res/fps/context_frames 等。
    """
    from app.services import project_longvideo_service

    try:
        task, segment_ids, total_frames = project_longvideo_service.generate(
            db, episode_id, body or EpisodeFilmGenerate()
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return EpisodeFilmGenerateOut(
        task_id=task.id, episode_id=episode_id,
        segment_count=len(segment_ids), total_frames=total_frames,
        message=f"连续长片任务已提交（{len(segment_ids)} 段, {total_frames} 帧）",
    )


# ─── P7 幕级视频（取代逐镜视频）─────────────────────────────────────


class EpisodeVideoGenerateBody(BaseModel):
    """幕级视频生成参数（P7.6）。"""
    per_duration: int | None = None  # 每幕目标时长（秒）：5/10/15/18，缺省用 15


@router.post("/episodes/{episode_id}/design-images/generate")
def generate_episode_designs(episode_id: UUID, body: EpisodeVideoGenerateBody | None = None,
                             db: Session = Depends(get_db)):
    """幕首/幕尾图生成（P7.7 阶段1）：只生成幕级设计图（幕首图/衔接图/幕尾图），不生成视频。

    per_duration：每幕目标时长（5/10/15/18s，缺省 15）。用户确认首尾帧后
    再点「生成幕级视频」触发阶段2。
    """
    if not db.get(Episode, episode_id):
        raise HTTPException(404, "幕不存在")
    try:
        rows, task, episodes = episode_video_service.generate_designs(
            db, episode_id,
            per_duration=(body.per_duration if body else None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "videos": [EpisodeVideoOut.model_validate(r) for r in rows],
        "task": TaskOut.model_validate(task),
        "episodes": [EpisodeOut.model_validate(e) for e in episodes],
    }


@router.post("/episodes/{episode_id}/video/generate")
def generate_episode_video(episode_id: UUID, body: EpisodeVideoGenerateBody | None = None,
                           db: Session = Depends(get_db)):
    """幕级视频生成（P7.7 阶段2）：要求首尾帧图已生成（先点「生成幕首/幕尾图」），只生成视频。

    per_duration：每幕目标时长（5/10/15/18s，缺省 15）。拆幕后返回全部幕。
    """
    if not db.get(Episode, episode_id):
        raise HTTPException(404, "幕不存在")
    try:
        rows, task, episodes = episode_video_service.generate(
            db, episode_id,
            per_duration=(body.per_duration if body else None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "videos": [EpisodeVideoOut.model_validate(r) for r in rows],
        "task": TaskOut.model_validate(task),
        "episodes": [EpisodeOut.model_validate(e) for e in episodes],
    }


@router.get("/episodes/{episode_id}/videos", response_model=list[EpisodeVideoOut])
def list_episode_videos(episode_id: UUID, db: Session = Depends(get_db)):
    """幕级视频段列表（按段号排序）。"""
    if not db.get(Episode, episode_id):
        raise HTTPException(404, "幕不存在")
    return episode_video_service.list_by_episode(db, episode_id)


@router.get("/episodes/{episode_id}/video-script")
def get_video_script(episode_id: UUID, db: Session = Depends(get_db)):
    """幕级时间轴分镜描述（未生成返回 null）。"""
    ep = db.get(Episode, episode_id)
    if not ep:
        raise HTTPException(404, "幕不存在")
    return {"video_script": ep.video_script}


# ─── 白模故事版：动作序列多镜头完整展示（2026-08-10，手动触发）──────────────


@router.get("/episodes/{episode_id}/action-sequences")
def list_action_sequences(episode_id: UUID, db: Session = Depends(get_db)):
    """动作序列列表：该幕的 sequence_key 标记 + 已生成的 ActionSequence 行。

    未生成时 sequences 为空，前端据 keys 展示「生成动作预览」按钮。
    """
    if not db.get(Episode, episode_id):
        raise HTTPException(404, "幕不存在")
    keys = action_sequence_service.list_sequence_keys(db, episode_id)
    sequences = action_sequence_service.list_by_episode(db, episode_id)
    return {
        "keys": keys,
        "sequences": [ActionSequenceOut.model_validate(s) for s in sequences],
    }


@router.post("/episodes/{episode_id}/action-sequences/{sequence_key}/generate")
def generate_action_sequence(episode_id: UUID, sequence_key: str,
                             db: Session = Depends(get_db)):
    """动作序列生成入口（手动触发）：白模模板图 + LLM 分组 → 逐组视频 → 硬切拼接。"""
    if not db.get(Episode, episode_id):
        raise HTTPException(404, "幕不存在")
    try:
        seq, task, segments = action_sequence_service.generate_template(
            db, episode_id, sequence_key,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "sequence": ActionSequenceOut.model_validate(seq),
        "task": TaskOut.model_validate(task),
        "segments": [SegmentOut.model_validate(s) for s in segments],
    }
