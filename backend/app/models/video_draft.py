"""AI 视频草稿（Video Lab 页签）：项目级独立视频生成记录。

支持纯文生视频（T2V）、首尾帧生视频（FL2VA）、参考视频+图片混合生视频（R2V）。
首尾帧/参考图/参考视频/资产参考均为可选，由生成任务按内容自动路由模型与节点。
"""
import uuid

from sqlalchemy import JSON, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.media import MediaStatus


class VideoDraft(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "video_draft"

    # 2026-08-11：AI 视频升级为独立功能（顶层导航入口），project_id 可空——
    # 纯文生/首尾帧/参考图/参考视频不依赖项目；仅当引用资产时关联项目
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)  # 用户原始提示词
    negative_prompt: Mapped[str | None] = mapped_column(Text)  # 负面提示词
    # LLM 优化后的最终提示词（R2V 链路含 <Picture N>/<Video N> 参考标签），
    # 为空时生成任务回退用原始 prompt
    enhanced_prompt: Mapped[str | None] = mapped_column(Text)
    first_frame_url: Mapped[str | None] = mapped_column(String(512))  # 首帧（FL2VA/锚定）
    last_frame_url: Mapped[str | None] = mapped_column(String(512))  # 尾帧（FL2VA）
    ref_image_urls: Mapped[list | None] = mapped_column(JSON)  # list[str] 参考图（R2V，≤9）
    ref_video_urls: Mapped[list | None] = mapped_column(JSON)  # list[str] 参考视频（R2V，≤3）
    asset_refs: Mapped[list | None] = mapped_column(JSON)  # list[str] 项目资产 id（封面作参考图）
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9", nullable=False)
    duration: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 秒（4/5/8/10/15）
    # 2026-08-23 二采验证：生成档位（480p/720p/768p，None=跟随项目）与二采来源草稿
    resolution: Mapped[str | None] = mapped_column(String(16))
    base_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video_draft.id", ondelete="SET NULL"), nullable=True
    )
    video_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
