"""动作序列（ActionSequence）：动作/打斗场景多镜头完整展示的产物记录。

背景（2026-08-10 白模故事版与多镜头动作展示方案）：
- 一段打斗在单个 5s 镜头内无法展示完 → 生成一张多格白模模板图作为动作流程视觉参考，
  LLM 按动作节拍把格子分组成 M 组（N 格一组 = 一个 5s 镜头），逐组生成视频后硬切
  拼接成一条完整动作视频，替换幕级长视频对应动作片段。
- 本表挂幕（episode）下，存模板图 + 格子分组 + 逐组视频 + 拼接产物。

字段说明：
- template_url：一张多格白模分镜表模板图（角色/场景/道具参考 + 白模风格 + 多格布局）
- grid_count：模板图格子数（LLM 按场景决定，如 6/9/12）
- groups：LLM 分组结果 JSON，每项 {cells:[格子序号], segment_id, shot_type, camera, prompt}
- videos_url：逐组视频 URL 列表（与 groups 一一对应）
- composed_url：硬切拼接后的完整动作视频
- 触发：手动（幕级看板"生成动作预览"按钮），不自动
"""
import uuid

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.media import MediaStatus


class ActionSequence(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "action_sequence"

    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=False
    )
    # 动作序列标记（Segment.action_sequence 的值，如 "as_1"），本序列覆盖的分镜据此聚合
    sequence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # 涉及的分镜 id（按镜头顺序）
    segment_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 白模模板图（多格分镜表）
    template_url: Mapped[str | None] = mapped_column(String(512))
    grid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # LLM 分组：[{"cells": [1,2], "segment_id": "...", "shot_type": "...", "camera": "...", "prompt": "..."}]
    groups: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 逐组视频 URL（顺序与 groups 一致）
    videos_url: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 拼接后的完整动作视频
    composed_url: Mapped[str | None] = mapped_column(String(512))
    composed_duration: Mapped[float | None] = mapped_column(Float)
    # 生成阶段：template_pending/template_done/videos_pending/composed_done 等用 status + 阶段字段
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)

    episode = relationship("Episode", back_populates="action_sequences")
