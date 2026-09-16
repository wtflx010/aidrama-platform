"""Novel 模型：存储上传的小说文本及分析结果。"""
import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class NovelAnalysisStatus(str, enum.Enum):
    pending = "pending"
    analyzing = "analyzing"
    done = "done"
    failed = "failed"


class Novel(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "novel"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    chapters_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    analysis_status: Mapped[str] = mapped_column(
        String(32), default=NovelAnalysisStatus.pending, nullable=False
    )
    analysis_result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL")
    )
    # 2026-08-11 AI 长篇小说写作：大纲（含各章 brief/写后 summary）+ 写作状态
    outline: Mapped[dict | None] = mapped_column(JSONB)
    writing_status: Mapped[str] = mapped_column(
        String(16), default="none", nullable=False
    )  # none | writing | done | failed
    # 2026-08-23：剧本海报（写剧本时自动由文生图生成，供剧本库卡片展示）
    poster_url: Mapped[str | None] = mapped_column(String(512), default=None)
    # 2026-08-23：确认式生成项目——写剧本后预生成分镜预览（{episodes, episode_count, segment_count}），
    # 不创建项目；用户确认并指定参数后再改编为项目。
    shot_plan: Mapped[dict | None] = mapped_column(JSONB)

    project = relationship("Project", foreign_keys=[project_id])
