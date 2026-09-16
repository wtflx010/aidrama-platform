"""视频模型路由原语：生视频统一走 MiniMax H3（minimax / minimax_ref）。

收敛自 video_service._resolve_shot_video_model 与 canvas_director._resolve_director_model，
两种语义原样保留（单镜 vs 导演台/长片选型规则不同），入口转发不改变行为。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_config import Model, ModelType


def resolve_shot_video_model(db: Session, model_id=None):
    """单镜视频模型路由（H3 唯一）。

    规则：显式 model_id 须启用；否则优先 minimax_ref（R2V 多图参考）；无则回落 minimax。
    """
    if model_id:
        m = db.get(Model, model_id)
        if not m or not m.is_enabled:
            raise ValueError("模型不存在或已停用")
        return m
    prefer_kind = "minimax_ref"
    m = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.video,
            Model.is_enabled.is_(True),
            Model.capability["video_kind"].astext == prefer_kind,
        ).order_by(Model.sort.asc())
    )
    if m is not None:
        return m
    from app.services.keyframe_service import _resolve_model
    return _resolve_model(db, model_id, ModelType.video, "video")


def resolve_director_model(db: Session, model_id=None) -> Model | None:
    """导演台 / 连续长片视频模型（显式优先；否则第一个启用的 MiniMax ComfyUI 视频模型）。"""
    if model_id:
        m = db.get(Model, model_id)
        if m and m.is_enabled and (m.capability or {}).get("video_kind") in ("minimax", "minimax_ref"):
            return m
    rows = db.scalars(
        select(Model).where(
            Model.model_type == "video",
            Model.is_enabled.is_(True),
            Model.provider_type == "comfyui",
        ).order_by(Model.is_default.desc(), Model.sort.asc())
    ).all()
    for m in rows:
        if (m.capability or {}).get("video_kind") in ("minimax", "minimax_ref"):
            return m
    return None
