"""成片评估 schema（P0-1）。"""
import uuid
from typing import Any

from pydantic import BaseModel, Field


class EpisodeEvalOut(BaseModel):
    id: uuid.UUID
    episode_id: uuid.UUID
    video_url: str | None = None
    scores: dict[str, float]
    rule_scores: dict[str, Any] = Field(default_factory=dict)
    report: str | None = None
    suggestions: list[dict] = Field(default_factory=list)
    status: str
    error: str | None = None
    created_at: Any = None


class ApplyFeedbackIn(BaseModel):
    """反哺闭环请求：对指定分镜应用评估建议。

    segment_indexes: 要处理的分镜序号（幕内 1-based，与分镜 index 一致）；
        为空 = 自动取评估建议里列出的所有分镜。
    regenerate_keyframes: True=清提示词缓存并为这些分镜重新派发关键帧生成；
        False=仅清提示词缓存（下次生成自动用新提示词），不动生成。
    """
    segment_indexes: list[int] | None = None
    regenerate_keyframes: bool = True


class ApplyFeedbackOut(BaseModel):
    ok: bool
    applied: list[dict] = Field(default_factory=list)
    message: str = ""
