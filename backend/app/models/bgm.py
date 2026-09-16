"""BgmTrack 模型：全局背景音乐轨道（2026-08-22 全局音频库改造）。

- project_id 可空（空 = 全局音乐，未绑定特定项目）
- 与项目多对多绑定（project_bgm 表）：一个音乐可被多个项目使用；
  项目删除仅解绑，音乐保留在全局音频库
- episode_id 保留：在项目中可进一步绑定到具体幕
"""
import uuid

from sqlalchemy import (
    Float, ForeignKey, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class BgmTrack(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "bgm_track"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL"), nullable=True
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("episode.id", ondelete="SET NULL"), nullable=True
    )
    emotion: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    audio_url: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.15, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="musicgen", nullable=False)

    project = relationship("Project", foreign_keys=[project_id])
    episode = relationship("Episode", foreign_keys=[episode_id])
    projects_bound = relationship(
        "ProjectBgm", back_populates="bgm", cascade="all, delete-orphan",
    )


class ProjectBgm(UUIDPkMixin, TimestampMixin, Base):
    """项目 ↔ 背景音乐 绑定（全局音频库）：项目删除仅解绑，音乐保留。"""
    __tablename__ = "project_bgm"
    __table_args__ = (UniqueConstraint("bgm_id", "project_id", name="uq_project_bgm"),)

    bgm_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bgm_track.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )

    bgm = relationship("BgmTrack", back_populates="projects_bound")
    project = relationship("Project", back_populates="bgms_bound")
