"""幕级视频（P7）：以幕为单位的视频段记录。

取代逐镜视频：一幕按分镜分组分成多段（每段 ≤4 镜、441 帧 ≈18.4s），
段内用时间轴分镜描述承载多个镜头的叙事，段间用首尾帧衔接。
"""
import uuid

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.media import MediaStatus


class EpisodeVideo(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "episode_video"

    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)  # 段号（1-based，成片拼接顺序）
    prompt: Mapped[str | None] = mapped_column(Text)  # 该段时间轴分镜 prompt（可审计）
    num_frames: Mapped[int] = mapped_column(Integer, default=441, nullable=False)
    frame_rate: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=1280, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=720, nullable=False)
    first_frame_url: Mapped[str | None] = mapped_column(String(512))  # 段首关键帧
    last_frame_url: Mapped[str | None] = mapped_column(String(512))  # 段尾关键帧（下一镜关键帧）
    video_url: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[float | None] = mapped_column(Float)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)

    episode = relationship("Episode", back_populates="episode_videos")
