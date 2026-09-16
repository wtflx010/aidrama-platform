from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.task import TaskStatus, TaskType
from app.schemas.keyframe import KeyframeOut
from app.schemas.video import VideoOut


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    type: TaskType
    target_type: str
    target_id: UUID
    model_id: UUID | None
    status: TaskStatus
    progress: int
    error: str | None
    result_url: str | None
    provider: str | None
    provider_task_id: str | None
    poll_url: str | None
    started_at: datetime | None
    finished_at: datetime | None
    last_heartbeat_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # 分镜级任务的目标坐标（generate_video/generate_keyframe 等）：
    # {episode_index, segment_index, title}，任务中心按此识别是第几幕第几镜
    segment_ref: dict | None = None


class GenerateResp(BaseModel):
    """生成接口统一返回：目标对象 + 关联任务。"""

    keyframe: KeyframeOut | None = None
    video: VideoOut | None = None
    task: TaskOut


class EpisodeExportOut(BaseModel):
    """剧集导出状态：该幕最新导出任务 + 可导出性判断（供前端展示准备状态）。"""

    task: TaskOut | None
    exportable: bool
    reason: str | None = None
    file_size: int | None = None
