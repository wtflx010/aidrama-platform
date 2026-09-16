"""视频 API：列表 / 生成 / 重跑 / 删除。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.task import GenerateResp
from app.schemas.upscale import UpscaleRequest
from app.schemas.video import VideoGenerate, VideoOut
from app.services import video_service

router = APIRouter()


@router.get("/segments/{segment_id}/videos", response_model=list[VideoOut])
def list_by_segment(segment_id: UUID, db: Session = Depends(get_db)):
    return video_service.list_by_segment(db, segment_id)


@router.post("/segments/{segment_id}/videos/generate", response_model=GenerateResp, status_code=201)
def generate(segment_id: UUID, payload: VideoGenerate, db: Session = Depends(get_db)):
    try:
        clip, task = video_service.generate(db, segment_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return GenerateResp(video=clip, task=task)


@router.post("/segments/{segment_id}/videos/upscale", status_code=201)
def upscale(segment_id: UUID, body: UpscaleRequest | None = None, db: Session = Depends(get_db)):
    """单一分镜超分：对该分镜最新成功视频发起 480p → 1080p（tier 可选提供）。

    请求体允许 {tier: "4x"|"2x"}（不传默认 4x）；返回 {task: {id}} 供前端轮询。
    """
    from app.services.upscale_service import find_source_clip, generate

    src = find_source_clip(db, segment_id)
    if src is None:
        raise HTTPException(400, "该分镜没有已生成成功的视频，无法超分")
    tier = body.tier if body else "4x"
    try:
        clip, task = generate(db, src, tier=tier)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"task": {"id": str(task.id)}}


@router.post("/videos/{video_id}/regenerate", response_model=GenerateResp, status_code=201)
def regenerate(video_id: UUID, payload: VideoGenerate, db: Session = Depends(get_db)):
    try:
        clip, task = video_service.regenerate(db, video_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return GenerateResp(video=clip, task=task)


@router.delete("/videos/{video_id}")
def delete(video_id: UUID, db: Session = Depends(get_db)):
    if not video_service.delete(db, video_id):
        raise HTTPException(404, "视频不存在")
    return {"ok": True}
