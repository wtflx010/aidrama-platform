"""配音与字幕模型。

- VoiceLine：分镜的对白/旁白 TTS 配音产物
- Subtitle：分镜字幕，与配音时间对齐（start_ms/end_ms）
"""
import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.media import MediaStatus


class VoiceLine(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "voice_line"
    __table_args__ = (
        Index("ix_voice_line_segment", "segment_id"),
        Index("ix_voice_line_segment_index", "segment_id", "line_index"),
    )

    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("segment.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    voice_id: Mapped[str | None] = mapped_column(String(64))
    audio_url: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[float | None] = mapped_column(Float)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)

    # 差异化配音：说话人 + 情绪 + instruct 指令 + 同镜顺序 + 旁白标记
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("asset.id", ondelete="SET NULL")
    )
    emotion: Mapped[str | None] = mapped_column(String(32))
    instruct_text: Mapped[str | None] = mapped_column(Text)
    line_index: Mapped[int | None] = mapped_column(Integer)
    is_narration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # P2-5 多语言配音：该条配音/字幕的语言（ISO 639-3，默认简体中文 zh-CN）
    language: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)

    segment = relationship("Segment", back_populates="voice_lines")


class Subtitle(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "subtitle"
    __table_args__ = (Index("ix_subtitle_segment", "segment_id"),)

    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("segment.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    segment = relationship("Segment", back_populates="subtitles")
