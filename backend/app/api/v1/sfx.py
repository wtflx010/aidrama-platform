"""SFX 音效管理 API：列出 / 生成 / 更新 / 删除。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Episode, Project
from app.models.segment import Segment
from app.models.sfx import SfxClip
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.sfx import SfxAnnotate, SfxGenerate, SfxOut, SfxUpdate, SfxUploadBody
from app.tasks.generate_sfx import generate_sfx_task

router = APIRouter()




@router.post("/audio/library/sfx/upload", response_model=SfxOut, status_code=201)
def upload_global_sfx(payload: SfxUploadBody, db: Session = Depends(get_db)):
    """上传音效到全局音频库。"""
    clip = SfxClip(
        project_id=payload.project_id,
        segment_id=None,
        sfx_type=(payload.sfx_type or "other").strip(),
        sfx_name=payload.sfx_name.strip(),
        audio_url=payload.audio_url,
        duration=payload.duration, volume=payload.volume,
        status="done", source="upload",
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)
    return clip

@router.get("/projects/{project_id}/sfx", response_model=list[SfxOut])
def list_project_sfx(project_id: UUID, db: Session = Depends(get_db)):
    """列出项目所有音效片段（按分镜顺序）。"""
    return db.scalars(
        select(SfxClip)
        .join(Segment, SfxClip.segment_id == Segment.id)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
        .order_by(Episode.index, Segment.index, SfxClip.start_time)
    ).all()


@router.get("/segments/{segment_id}/sfx", response_model=list[SfxOut])
def list_segment_sfx(segment_id: UUID, db: Session = Depends(get_db)):
    """列出单个分镜的音效。"""
    return db.scalars(
        select(SfxClip)
        .where(SfxClip.segment_id == segment_id)
        .order_by(SfxClip.start_time)
    ).all()


@router.post("/projects/{project_id}/sfx/generate", status_code=201)
def trigger_generate_sfx(project_id: UUID, payload: SfxAnnotate, db: Session = Depends(get_db)):
    """触发项目音效批量生成（LLM 标注 + Freesound 下载）。"""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    task = Task(
        project_id=project_id,
        type=TaskType.generate_sfx,
        target_type="sfx",
        target_id=project_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    generate_sfx_task.delay(
        str(task.id), str(project_id),
        [str(sid) for sid in payload.segment_ids] if payload.segment_ids else None,
        str(payload.model_id) if payload.model_id else None,
    )
    return {"task_id": str(task.id), "project_id": str(project_id)}


@router.post("/segments/{segment_id}/sfx", response_model=SfxOut, status_code=201)
def create_sfx_clip(segment_id: UUID, payload: SfxGenerate, db: Session = Depends(get_db)):
    """手动添加单条音效（需 segment_id 与 payload.segment_id 一致）。"""
    segment = db.get(Segment, segment_id)
    if not segment:
        raise HTTPException(404, "分镜不存在")
    if str(payload.segment_id) != str(segment_id):
        raise HTTPException(400, "segment_id 不一致")

    clip = SfxClip(
        segment_id=segment_id,
        sfx_type=payload.sfx_type,
        sfx_name=payload.sfx_name,
        start_time=payload.start_time,
        volume=payload.volume,
        status="pending",  # 手动添加，未自动下载音频
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)
    return clip


@router.patch("/sfx/{sfx_id}", response_model=SfxOut)
def update_sfx(sfx_id: UUID, payload: SfxUpdate, db: Session = Depends(get_db)):
    """更新音效片段参数（音量/起始时间/时长）。"""
    clip = db.get(SfxClip, sfx_id)
    if not clip:
        raise HTTPException(404, "音效片段不存在")
    if payload.volume is not None:
        clip.volume = payload.volume
    if payload.start_time is not None:
        clip.start_time = payload.start_time
    if payload.duration is not None:
        clip.duration = payload.duration
    db.commit()
    db.refresh(clip)
    return clip


@router.delete("/sfx/{sfx_id}")
def delete_sfx(sfx_id: UUID, db: Session = Depends(get_db)):
    """删除音效片段。"""
    clip = db.get(SfxClip, sfx_id)
    if not clip:
        raise HTTPException(404, "音效片段不存在")
    db.delete(clip)
    db.commit()
    return {"ok": True}
