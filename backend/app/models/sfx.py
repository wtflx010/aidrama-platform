"""SfxClip 模型：全局音效片段（2026-08-22 全局音频库改造）。

- segment_id 可空：音效可先入全局音频库，再挂到分镜使用
- project_id 可空（空 = 全局音效）：项目删除时解绑保留，音效留在库中
"""
import uuid

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class SfxClip(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "sfx_clip"

    # 音效库：可先归库（project_id 空）再挂分镜；挂分镜后期望为非空
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL"), nullable=True
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("segment.id", ondelete="SET NULL"), nullable=True
    )
    sfx_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sfx_name: Mapped[str] = mapped_column(String(128), nullable=False)
    audio_url: Mapped[str | None] = mapped_column(String(512))
    start_time: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="freesound", nullable=False)

    project = relationship("Project", foreign_keys=[project_id])
    segment = relationship("Segment", foreign_keys=[segment_id])
