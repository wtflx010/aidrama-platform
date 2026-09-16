"""动作序列（白模故事版）schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.media import MediaStatus


class ActionSequenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    episode_id: UUID
    sequence_key: str
    segment_ids: list
    template_url: str | None
    grid_count: int
    groups: list
    videos_url: list
    composed_url: str | None
    composed_duration: float | None
    status: MediaStatus
    error: str | None
    created_at: datetime
    updated_at: datetime
