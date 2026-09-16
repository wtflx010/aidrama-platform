"""引擎内核层 · 模型解析：对话/生图模型解析（复用 keyframe_service 规则）。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_config import Model, ModelType


def _resolve_chat_model(db: Session, model_id) -> Model:
    """解析对话模型：显式 model_id > 默认启用文本模型 > 任意启用文本模型。"""
    if model_id:
        m = db.get(Model, model_id)
        if not m:
            raise ValueError("对话模型不存在")
        if m.model_type != ModelType.text:
            raise ValueError("对话模型必须为文本类型")
        if not m.is_enabled:
            raise ValueError(f"对话模型「{m.name}」已停用，请先启用")
        return m
    m = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.text,
            Model.is_default.is_(True),
            Model.is_enabled.is_(True),
        )
    )
    if m:
        return m
    m = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.text,
            Model.is_enabled.is_(True),
        ).order_by(Model.sort.asc())
    )
    if m is None:
        raise ValueError("未配置可用的文本对话模型，请先在模型管理启用")
    return m


def _resolve_image_model(db: Session, scene_code: str = "keyframe") -> Model:
    """解析生图模型（复用关键帧链路的模型解析规则：默认+生效优先，禁止静默回退）。"""
    from app.services.keyframe_service import _resolve_model
    return _resolve_model(db, None, ModelType.image, scene_code)
