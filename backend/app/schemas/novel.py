"""Novel 相关 schema。"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator

# 支持的屏幕尺寸（与 project schema 一致）
_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}


class NovelUpload(BaseModel):
    title: str
    text: str


class AnalyzeBody(BaseModel):
    model_id: UUID | None = None


class AdaptBody(BaseModel):
    model_id: UUID | None = None
    # 分镜时长上限（秒）：5/10/15，LLM 按镜头内容在 1~上限内配置每镜时长（默认 15 = 最长）
    per_duration: int = 15
    # 视觉风格（LLM 改编剧本用）；不传时后端按 style_id/art_style_prompt 派生，再没有则写实兜底
    style: str | None = None
    # P8 项目风格/尺寸：落到 Project，后续资产生成/关键帧保持一致
    style_id: UUID | None = None
    art_style_prompt: str | None = None
    aspect_ratio: str = "16:9"
    # 视频分辨率（480p/720p/768p），后续生成的视频统一按此档位执行
    resolution: str = "720p"
    # 项目级视频生成参数（fps/steps/cfg/turbo/seed 等，与 project schema 一致）
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
        if v not in {"0.1mp", "0.2mp", "0.25mp", "0.3mp", "0.4mp", "0.5mp", "0.6mp", "0.7mp", "0.8mp", "0.9mp", "1.0mp", "480p", "720p", "768p"}:
            raise ValueError("不支持的分辨率，可选：0.1MP~1.0MP / 480p / 720p / 768p")
        return v

    @field_validator("per_duration")
    @classmethod
    def validate_per_duration(cls, v: int) -> int:
        if v not in {5, 10, 15}:
            raise ValueError("分镜时长上限仅支持 5/10/15 秒")
        return v


class AdaptContinuationBody(BaseModel):
    """P6 章节续接追加请求体。"""
    project_id: UUID
    chapter_start: int
    chapter_end: int
    model_id: UUID | None = None


class AppendChaptersBody(BaseModel):
    """追加后续章节请求体：把新章节文本合并进小说 raw_text，供续接追加改编。"""
    text: str


class ChapterOut(BaseModel):
    """章节列表项：index/title/是否已追加到该项目。"""
    index: int
    title: str
    processed: bool = False


class NovelPosterUpload(BaseModel):
    """剧本海报上传请求体（base64 图片，与资产封面上传同模式）。"""
    filename: str
    data_base64: str  # 不带 data: 前缀的纯 base64


class NovelOut(BaseModel):
    id: UUID
    title: str
    chapters_count: int
    word_count: int
    analysis_status: str
    error: str | None = None
    project_id: UUID | None = None
    # 2026-08-23 剧本海报（写剧本时自动由文生图生成，供剧本库卡片展示）
    poster_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NovelDetail(NovelOut):
    analysis_result: dict[str, Any] | None = None
    raw_text: str | None = None
    # 2026-08-23 确认式生成项目：写剧本后预生成的分镜预览（{episodes, episode_count, segment_count}）
    shot_plan: dict | None = None
