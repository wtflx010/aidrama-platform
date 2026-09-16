from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.media import MediaStatus


def _validate_num_frames(v: int) -> int:
    if v < 9 or v > 441:
        raise ValueError("num_frames 须在 9~441 之间")
    if (v - 1) % 8 != 0:
        raise ValueError("num_frames 必须满足 8n+1（如 81/121/161/241/441）")
    return v


class VideoGenerate(BaseModel):
    keyframe_id: UUID | None = None
    model_id: UUID | None = None
    num_frames: int = 121
    frame_rate: int = 24
    # 尺寸可选：前端不传时由后端按项目 aspect_ratio 计算（9:16→768x1344 等）。
    # 2026-08-10 修复：之前默认 1280x720，项目选 9:16 时仍生成横屏视频。
    width: int | None = None
    height: int | None = None
    prompt: str | None = None

    @field_validator("num_frames")
    @classmethod
    def _check_frames(cls, v: int) -> int:
        return _validate_num_frames(v)


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    segment_id: UUID
    keyframe_id: UUID | None
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
    model_id: UUID | None
    task_id: UUID | None
    error: str | None
    # 视频超分（2026-08-23）：True=480p 原片超分后的高清版，前端播放/导出优先取它
    is_upscaled: bool = False
    upscale_of_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
