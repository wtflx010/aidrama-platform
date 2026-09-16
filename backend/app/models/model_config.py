import enum

from sqlalchemy import Boolean, Enum, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class ProviderType(str, enum.Enum):
    openai_compatible = "openai_compatible"
    http_poll = "http_poll"
    openai_tts = "openai_tts"
    comfyui = "comfyui"


class ModelType(str, enum.Enum):
    text = "text"
    image = "image"
    video = "video"
    tts = "tts"


class Model(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "model"
    __table_args__ = (Index("ix_model_type_enabled", "model_type", "is_enabled"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_type: Mapped[ProviderType] = mapped_column(
        Enum(ProviderType, name="provider_type"), nullable=False
    )
    model_type: Mapped[ModelType] = mapped_column(Enum(ModelType, name="model_type"), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    capability: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    credits_per_unit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scene_codes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    http_poll_config: Mapped[dict | None] = mapped_column(JSONB)
