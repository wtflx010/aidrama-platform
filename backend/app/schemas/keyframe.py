from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.media import MediaStatus


class KeyframeGenerate(BaseModel):
    prompt: str
    model_id: UUID | None = None
    size: str | None = None
    ratio: str | None = None
    use_reference: bool = True  # 默认启用角色/场景参考图走 img2img
    ref_image_urls: list[str] | None = None  # 显式指定参考图，覆盖自动拉取


class KeyframeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    segment_id: UUID
    index: int
    prompt: str
    image_url: str | None
    status: MediaStatus
    used_as_video_first_frame: bool
    model_id: UUID | None
    task_id: UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime
