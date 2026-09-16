"""成片评估模型（P0-1）：按幕保存 LLM+规则的四维评分、报告与反哺建议。

对标 Higgsfield Virality Predictor（brain_activity）：对一集成片打
钩子/注意力/留存/病毒性四维分，并产出逐分镜反哺建议。
"""
import enum
import uuid

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class EvalStatus(str, enum.Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class EpisodeEval(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "episode_eval"

    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=False
    )
    # 被评估成片/片段 URL（逗号分隔），供前端回看
    video_url: Mapped[str | None] = mapped_column(Text)
    # 四维评分 1~10：{hook, attention, retention, virality, overall}
    scores: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # 规则分明细（可审计）：{hook_rule:{...}, pacing:{...}, details:{...}}
    rule_scores: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # LLM 完整报告（文字，中文）
    report: Mapped[str | None] = mapped_column(Text)
    # 反哺建议：[{segment_index, issue, suggestion}]
    suggestions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[EvalStatus] = mapped_column(
        Enum(EvalStatus, name="eval_status"), default=EvalStatus.running, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)

    episode = relationship("Episode", back_populates="episode_evals")
