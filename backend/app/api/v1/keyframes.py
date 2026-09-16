"""关键帧 API：列表 / 生成 / 重跑 / 删除。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.keyframe import KeyframeGenerate, KeyframeOut
from app.schemas.task import GenerateResp
from app.services import keyframe_service

router = APIRouter()


@router.get("/segments/{segment_id}/keyframes", response_model=list[KeyframeOut])
def list_by_segment(segment_id: UUID, db: Session = Depends(get_db)):
    return keyframe_service.list_by_segment(db, segment_id)


@router.post("/segments/{segment_id}/keyframes/generate", response_model=GenerateResp, status_code=201)
def generate(segment_id: UUID, payload: KeyframeGenerate, db: Session = Depends(get_db)):
    try:
        kf, task = keyframe_service.generate(db, segment_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return GenerateResp(keyframe=kf, task=task)


@router.post("/keyframes/{keyframe_id}/regenerate", response_model=GenerateResp, status_code=201)
def regenerate(keyframe_id: UUID, payload: KeyframeGenerate, db: Session = Depends(get_db)):
    try:
        kf, task = keyframe_service.regenerate(db, keyframe_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return GenerateResp(keyframe=kf, task=task)


@router.delete("/keyframes/{keyframe_id}")
def delete(keyframe_id: UUID, db: Session = Depends(get_db)):
    if not keyframe_service.delete(db, keyframe_id):
        raise HTTPException(404, "关键帧不存在")
    return {"ok": True}
