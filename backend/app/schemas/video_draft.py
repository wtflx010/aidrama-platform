"""AI 视频草稿（Video Lab 页签）schema。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.media import MediaStatus


class VideoDraftCreate(BaseModel):
    """AI 视频为独立功能：project_id 可选（仅引用资产时关联项目）。"""
    project_id: UUID | None = None
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str | None = None
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    ref_image_urls: list[str] = Field(default_factory=list)
    ref_video_urls: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    aspect_ratio: str = "16:9"
    duration: Literal[5, 8, 10, 15] = 5  # 秒（仅允许 5/8/10/15）
    # 2026-08-23 二采验证：生成档位 480p/720p/768p；None = 跟随项目/模型默认
    resolution: str | None = None


class VideoDraftUpdate(BaseModel):
    prompt: str | None = None
    negative_prompt: str | None = None
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    ref_image_urls: list[str] | None = None
    ref_video_urls: list[str] | None = None
    asset_refs: list[str] | None = None
    aspect_ratio: str | None = None
    duration: Literal[5, 8, 10, 15] | None = None
    resolution: str | None = None


class VideoDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    prompt: str
    negative_prompt: str | None
    enhanced_prompt: str | None
    first_frame_url: str | None
    last_frame_url: str | None
    ref_image_urls: list[str] | None = None
    ref_video_urls: list[str] | None = None
    asset_refs: list[str] | None = None
    aspect_ratio: str
    duration: int
    resolution: str | None = None
    base_draft_id: UUID | None = None
    video_url: str | None
    status: MediaStatus
    model_id: UUID | None
    task_id: UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class VideoDraftGenerateResp(BaseModel):
    task_id: UUID


class VideoDraftRegenResp(BaseModel):
    """二采返回新草稿与任务（前端据此刷新并展示）。"""
    draft_id: UUID
    task_id: UUID


class VideoDraftFileUpload(BaseModel):
    """base64 文件上传（与资产上传一致，避免 python-multipart 依赖）。"""
    filename: str
    data_base64: str  # 不带 data: 前缀的纯 base64


class VideoDraftUploadResp(BaseModel):
    url: str


class VideoDraftEnhanceBody(BaseModel):
    """LLM 优化请求：携带本次生成的全部参考素材与参数，供标签对齐与语义注入。"""
    project_id: UUID | None = None  # 资产参考所属项目（可选）
    prompt: str
    negative_prompt: str | None = None
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    ref_image_urls: list[str] = Field(default_factory=list)
    ref_video_urls: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    aspect_ratio: str = "16:9"
    duration: Literal[5, 8, 10, 15] = 5


class VideoDraftEnhanceOut(BaseModel):
    enhanced_prompt: str
    negative_prompt: str


class VideoDraftGenerateResp(BaseModel):
    task_id: UUID
