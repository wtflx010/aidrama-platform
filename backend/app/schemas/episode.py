from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EpisodeCreate(BaseModel):
    title: str = "新幕"
    synopsis: str | None = None
    index: int | None = None  # 不传则追加到末尾


class EpisodeUpdate(BaseModel):
    title: str | None = None
    synopsis: str | None = None
    status: str | None = None


class EpisodeReorderBody(BaseModel):
    ordered_ids: list[UUID]


class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    index: int
    title: str
    synopsis: str | None
    status: str
    # P7 幕级视频：时间轴分镜描述 + 整体状态（none/running/succeeded/failed/partial）
    video_script: str | None = None
    video_status: str = "none"
    # 项目页签连续长片（AIMixer 导演台段间衔接，一集一条无缝整片）
    continuous_film_url: str | None = None
    continuous_film_duration: float | None = None
    continuous_film_status: str = "none"
    created_at: datetime
    updated_at: datetime
