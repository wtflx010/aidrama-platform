"""幕级视频（P7）schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.media import MediaStatus


class EpisodeVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    episode_id: UUID
    index: int
    prompt: str | None
    num_frames: int
    frame_rate: int
    width: int
    height: int
    first_frame_url: str | None
    last_frame_url: str | None
    video_url: str | None
    duration: float | None
    status: MediaStatus
    task_id: UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime
