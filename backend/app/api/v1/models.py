from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.model_config import ModelBrief
from app.services import model_service

router = APIRouter()


@router.get("", response_model=list[ModelBrief])
def list_user_models(
    model_type: str | None = None,
    scene_code: str | None = None,
    db: Session = Depends(get_db),
):
    """用户侧：返回 enabled 且（可选）匹配 model_type / scene_code 的模型。"""
    return model_service.list_models(
        db, model_type=model_type, scene_code=scene_code, enabled_only=True
    )


@router.get("/{model_id}/capability")
def capability(model_id: UUID, db: Session = Depends(get_db)):
    m = model_service.get(db, model_id)
    if not m:
        raise HTTPException(404, "模型不存在")
    return {"capability": m.capability, "http_poll_config": m.http_poll_config}
