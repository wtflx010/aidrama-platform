from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

T = TypeVar("T")


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


class IdResp(BaseModel):
    id: UUID


class ErrorResp(BaseModel):
    code: str
    message: str


class Timestamps(BaseModel):
    created_at: datetime
    updated_at: datetime
