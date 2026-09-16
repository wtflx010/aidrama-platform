"""美术风格预设 schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArtStyleBase(BaseModel):
    name: str
    category: str
    prompt_fragment: str
    description: str | None = None
    cover_url: str | None = None
    reference_images: list[str] = []
    sort_order: int = 0


class ArtStyleCreate(ArtStyleBase):
    is_builtin: bool = False


class ArtStyleUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    prompt_fragment: str | None = None
    description: str | None = None
    cover_url: str | None = None
    reference_images: list[str] | None = None
    sort_order: int | None = None


class ArtStyleOut(ArtStyleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_builtin: bool
    created_at: datetime
    updated_at: datetime
