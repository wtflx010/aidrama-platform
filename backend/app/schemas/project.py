from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.project import ProjectStatus

# 支持的屏幕尺寸
_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}
# 视频分辨率档位（创建项目档案选择，后续视频按此档位执行）
# 0.1MP~1.0MP 为 H3 官方级联档（32 倍数对齐）；保留 480p/720p/768p 兼容档
_RESOLUTIONS = {"0.1mp", "0.2mp", "0.25mp", "0.3mp", "0.4mp", "0.5mp", "0.6mp", "0.7mp", "0.8mp", "0.9mp", "1.0mp", "480p", "720p", "768p"}


def _validate_resolution(v: str) -> str:
    if v not in _RESOLUTIONS and v.lower() != "hd":  # "HD" 为旧数据兼容
        raise ValueError(f"不支持的分辨率：{v}，可选：0.1MP~1.0MP / 480p / 720p / 768p")
    return v


class ProjectCreate(BaseModel):
    title: str
    synopsis: str | None = None
    script: str | None = None
    aspect_ratio: str = "16:9"
    # 视频分辨率（480p/720p/768p），后续生成的视频统一按此档位执行
    resolution: str = "720p"
    style_id: UUID | None = None
    art_style_prompt: str | None = None
    rules: str | None = None
    # 项目级视频生成参数（分镜级 gen_params 显式设置时优先）
    video_params: dict | None = None

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str) -> str:
        if v not in _ASPECT_RATIOS:
            raise ValueError(f"不支持的屏幕尺寸：{v}，可选：{', '.join(sorted(_ASPECT_RATIOS))}")
        return v

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, v: str) -> str:
        return _validate_resolution(v)


class ProjectUpdate(BaseModel):
    title: str | None = None
    synopsis: str | None = None
    script: str | None = None
    aspect_ratio: str | None = None
    resolution: str | None = None
    status: ProjectStatus | None = None
    cover_url: str | None = None
    style_id: UUID | None = None
    art_style_prompt: str | None = None
    rules: str | None = None
    video_params: dict | None = None

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str | None) -> str | None:
        if v is not None and v not in _ASPECT_RATIOS:
            raise ValueError(f"不支持的屏幕尺寸：{v}，可选：{', '.join(sorted(_ASPECT_RATIOS))}")
        return v

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, v: str | None) -> str | None:
        if v is not None:
            return _validate_resolution(v)
        return v


class AIGenerateBody(BaseModel):
    """一句话生成项目请求体。"""
    synopsis: str
    model_id: UUID | None = None
    episode_count: int | None = None  # 提示 LLM 生成多少幕；不传则 LLM 自决
    # 分镜时长上限（5/10/15s），LLM 按镜头内容在 1~上限内配置每镜时长
    per_duration: int | None = None
    aspect_ratio: str = "16:9"
    # 视频分辨率（480p/720p/768p），后续生成的视频统一按此档位执行
    resolution: str = "720p"
    style_id: UUID | None = None
    art_style_prompt: str | None = None
    video_params: dict | None = None

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str) -> str:
        if v not in _ASPECT_RATIOS:
            raise ValueError(f"不支持的屏幕尺寸：{v}，可选：{', '.join(sorted(_ASPECT_RATIOS))}")
        return v

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, v: str) -> str:
        return _validate_resolution(v)

    @field_validator("per_duration")
    @classmethod
    def validate_per_duration(cls, v: int | None) -> int | None:
        if v is not None and v not in {5, 10, 15}:
            raise ValueError("分镜时长上限仅支持 5/10/15 秒")
        return v


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    synopsis: str | None
    script: str | None
    aspect_ratio: str
    resolution: str
    video_params: dict = {}
    status: ProjectStatus
    cover_url: str | None
    style_id: UUID | None = None
    art_style_prompt: str | None = None
    rules: str | None = None
    # P6 章节续接追加：来源小说 + 已改编到的章节号（断点）
    source_novel_id: UUID | None = None
    processed_upto_chapter: int | None = None
    created_at: datetime
    updated_at: datetime
