"""Sfx 音效相关 schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SfxAnnotate(BaseModel):
    """LLM 自动标注分镜音效请求。"""
    segment_ids: list[UUID] | None = None  # 不传则标注项目所有分镜
    model_id: UUID | None = None


class SfxGenerate(BaseModel):
    """单个音效生成请求（手动或自动触发）。"""
    segment_id: UUID
    sfx_type: str  # footsteps/door/rain/wind/explosion/...
    sfx_name: str  # 显示名称
    start_time: float = Field(default=0.0, ge=0.0)
    duration: float | None = Field(default=None, ge=0.0)  # 不传则使用下载音效实际时长
    volume: float = Field(default=0.5, ge=0.0, le=1.0)


class SfxUploadBody(BaseModel):
    """音效上传到音频库。"""
    project_id: UUID | None = None
    sfx_type: str = "other"
    sfx_name: str
    audio_url: str
    duration: float = 0.0
    volume: float = 0.5


class SfxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    segment_id: UUID | None
    sfx_type: str
    sfx_name: str
    audio_url: str | None
    start_time: float
    duration: float
    volume: float
    status: str
    error: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class SfxUpdate(BaseModel):
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    start_time: float | None = Field(default=None, ge=0.0)
    duration: float | None = None
