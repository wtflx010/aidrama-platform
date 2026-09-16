"""BGM 相关 schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BgmGenerate(BaseModel):
    """BGM 生成请求。"""
    episode_id: UUID | None = None  # 不传则生成项目所有幕的 BGM
    project_id: UUID | None = None  # 2026-08-22 全局音频库：可绑定到项目（可空=纯全局）
    emotion: str | None = None  # 不传则自动分析
    duration: int | None = None  # 不传则按幕总时长估算
    prompt: str | None = None  # 不传则按 emotion 自动生成


class BgmUploadBody(BaseModel):
    """BGM 上传到音频库。"""
    project_id: UUID | None = None
    emotion: str = "平静"
    prompt: str | None = None
    audio_url: str
    duration: float = 0.0


class BgmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    episode_id: UUID | None
    emotion: str
    prompt: str
    audio_url: str | None
    duration: float
    volume: float
    status: str
    error: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class BgmUpdate(BaseModel):
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    emotion: str | None = None
    prompt: str | None = None
