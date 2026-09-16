"""BGM 管理 API：列出 / 生成 / 更新音量 / 删除。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.bgm import BgmTrack
from app.models.project import Project
from app.models.sfx import SfxClip
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.bgm import BgmGenerate, BgmOut, BgmUpdate, BgmUploadBody
from app.tasks.generate_bgm import generate_bgm_task

router = APIRouter()



@router.get("/audio/library", response_model=dict)
def audio_library(db: Session = Depends(get_db)):
    """全局音频库：所有 BGM + 所有音效（项目删除仅解绑，音频保留在库）。"""
    bgms = db.scalars(
        select(BgmTrack).order_by(BgmTrack.created_at.desc(), BgmTrack.id.desc())
    ).all()
    sfxs = db.scalars(
        select(SfxClip).order_by(SfxClip.created_at.desc(), SfxClip.id.desc())
    ).all()
    return {
        "bgm": [
            {
                "id": str(b.id), "project_id": str(b.project_id) if b.project_id else None,
                "episode_id": str(b.episode_id) if b.episode_id else None,
                "emotion": b.emotion, "audio_url": b.audio_url,
                "duration": b.duration, "volume": b.volume, "status": b.status,
                "source": b.source, "prompt": b.prompt,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in bgms
        ],
        "sfx": [
            {
                "id": str(s.id), "project_id": str(s.project_id) if s.project_id else None,
                "segment_id": str(s.segment_id) if s.segment_id else None,
                "sfx_type": s.sfx_type, "sfx_name": s.sfx_name, "audio_url": s.audio_url,
                "duration": s.duration, "volume": s.volume, "status": s.status,
                "source": s.source,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sfxs
        ],
    }



@router.post("/audio/library/bgm/generate", status_code=201)
def generate_global_bgm(payload: BgmGenerate, db: Session = Depends(get_db)):
    """全局 BGM 一键生成：提示词 → MusicGen 出音乐（可绑定到项目，可空）。"""
    from app.services import bgm_service
    emotion = (payload.emotion or "平静").strip() or "平静"
    duration = payload.duration or 20
    prompt = (payload.prompt or "").strip() or bgm_service.emotion_to_music_prompt(emotion, duration)
    try:
        audio_path, dur = bgm_service.generate_bgm_track(prompt, duration)
    except Exception as e:
        raise HTTPException(400, f"BGM 生成失败: {e}")
    import app.config as _cfg
    base = _cfg.settings.static_base_url
    track = BgmTrack(
        project_id=payload.project_id,
        emotion=emotion, prompt=prompt,
        audio_url=f"{base}/media/{audio_path}",
        duration=dur, status="done",
    )
    db.add(track)
    db.flush()
    if payload.project_id is not None:
        from app.models.bgm import ProjectBgm
        if db.scalar(
            select(ProjectBgm).where(ProjectBgm.bgm_id == track.id, ProjectBgm.project_id == payload.project_id)
        ) is None:
            db.add(ProjectBgm(bgm_id=track.id, project_id=payload.project_id))
    db.commit()
    db.refresh(track)
    return track


@router.post("/audio/library/bgm/upload", response_model=BgmOut, status_code=201)
def upload_global_bgm(payload: BgmUploadBody, db: Session = Depends(get_db)):
    """上传 BGM 到音频库（audio_url 由 /uploads/audio 返回）。"""
    track = BgmTrack(
        project_id=payload.project_id,
        emotion=(payload.emotion or "平静").strip() or "平静",
        prompt=payload.prompt or "用户上传",
        audio_url=payload.audio_url, duration=payload.duration or 0,
        status="done", source="upload",
    )
    db.add(track)
    db.flush()
    if payload.project_id is not None:
        from app.models.bgm import ProjectBgm
        db.add(ProjectBgm(bgm_id=track.id, project_id=payload.project_id))
    db.commit()
    db.refresh(track)
    return track


@router.get("/projects/{project_id}/bgm", response_model=list[BgmOut])
def list_bgm(project_id: UUID, db: Session = Depends(get_db)):
    """列出项目所有 BGM 轨道（按幕顺序）。"""
    return db.scalars(
        select(BgmTrack)
        .where(BgmTrack.project_id == project_id)
        .order_by(BgmTrack.created_at.asc())
    ).all()


@router.post("/projects/{project_id}/bgm/generate", status_code=201)
def trigger_generate_bgm(project_id: UUID, payload: BgmGenerate, db: Session = Depends(get_db)):
    """触发 BGM 生成（异步任务）。

    - 不传 episode_id：为项目所有幕生成
    - 传 episode_id：只生成该幕
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    task = Task(
        project_id=project_id,
        type=TaskType.generate_bgm,
        target_type="bgm",
        target_id=project_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    generate_bgm_task.delay(
        str(task.id), str(project_id),
        str(payload.episode_id) if payload.episode_id else None,
    )
    return {"task_id": str(task.id), "project_id": str(project_id)}


@router.patch("/bgm/{bgm_id}", response_model=BgmOut)
def update_bgm(bgm_id: UUID, payload: BgmUpdate, db: Session = Depends(get_db)):
    """更新 BGM 轨道参数（音量等）。"""
    track = db.get(BgmTrack, bgm_id)
    if not track:
        raise HTTPException(404, "BGM 轨道不存在")
    if payload.volume is not None:
        track.volume = payload.volume
    if payload.emotion is not None:
        track.emotion = payload.emotion
    if payload.prompt is not None:
        track.prompt = payload.prompt
    db.commit()
    db.refresh(track)
    return track


@router.delete("/bgm/{bgm_id}")
def delete_bgm(bgm_id: UUID, db: Session = Depends(get_db)):
    """删除 BGM 轨道。"""
    track = db.get(BgmTrack, bgm_id)
    if not track:
        raise HTTPException(404, "BGM 轨道不存在")
    db.delete(track)
    db.commit()
    return {"ok": True}
