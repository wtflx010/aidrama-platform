from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.model_config import ModelType, ProviderType


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    provider_type: ProviderType
    model_type: ModelType
    provider_name: str
    endpoint: str
    api_key_ref: str  # 仅变量名，非密钥本身
    model_id: str
    capability: dict
    credits_per_unit: int
    scene_codes: list[str]
    is_enabled: bool
    is_default: bool
    sort: int
    http_poll_config: dict | None
    created_at: datetime
    updated_at: datetime


class ModelBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    provider_type: ProviderType
    model_type: ModelType
    model_id: str
    is_default: bool
    sort: int


class ModelCreate(BaseModel):
    name: str
    provider_type: ProviderType
    model_type: ModelType
    provider_name: str
    endpoint: str
    api_key_ref: str
    model_id: str
    capability: dict = {}
    credits_per_unit: int = 0
    scene_codes: list[str] = []
    is_enabled: bool = True
    is_default: bool = False
    sort: int = 0
    http_poll_config: dict | None = None


class ModelUpdate(BaseModel):
    name: str | None = None
    provider_type: ProviderType | None = None
    model_type: ModelType | None = None
    provider_name: str | None = None
    endpoint: str | None = None
    api_key_ref: str | None = None  # 留空则不改
    model_id: str | None = None
    capability: dict | None = None
    credits_per_unit: int | None = None
    scene_codes: list[str] | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None
    sort: int | None = None
    http_poll_config: dict | None = None


class ModelTestResult(BaseModel):
    ok: bool
    message: str


class SaveApiKeyBody(BaseModel):
    """把 API Key 写入 backend/.env（仅限 *_KEY / *_TOKEN 命名，避免写入任意变量）。"""
    env_name: str
    api_key: str


class ToggleBody(BaseModel):
    is_enabled: bool


class SetDefaultBody(BaseModel):
    scene_code: str | None = None  # P0 按 model_type 清零，scene_code 仅供 P1 细化
