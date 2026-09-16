import enum
import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class MediaStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Keyframe(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "keyframe"

    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("segment.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    used_as_video_first_frame: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)

    segment = relationship("Segment", back_populates="keyframes")


class VideoClip(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "video_clip"

    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("segment.id", ondelete="CASCADE"), nullable=False
    )
    keyframe_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("keyframe.id", ondelete="SET NULL"))
    prompt: Mapped[str | None] = mapped_column(Text)
    num_frames: Mapped[int] = mapped_column(Integer, default=121, nullable=False)
    frame_rate: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=1280, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=720, nullable=False)
    first_frame_url: Mapped[str | None] = mapped_column(String(512))
    last_frame_url: Mapped[str | None] = mapped_column(String(512))
    video_url: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[float | None] = mapped_column(Float)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    # 视频超分标记（2026-08-23）：True=480p 原片超分后的高清版（播放/导出优先取它）
    is_upscaled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 超分来源的原片 clip id（is_upscaled=True 时有值）
    upscale_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_clip.id", ondelete="SET NULL"), index=True
    )

    segment = relationship("Segment", back_populates="videos")
