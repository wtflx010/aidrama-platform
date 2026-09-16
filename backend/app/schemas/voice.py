"""配音与字幕相关 schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.media import MediaStatus
from app.schemas.task import TaskOut


class VoiceGenerate(BaseModel):
    # P2：text/voice_id 不再用（自动按 dialogue_lines + 角色 voice_profile 解析）
    # 保留 model_id 可选指定 TTS 模型
    model_id: UUID | None = None


class VoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    segment_id: UUID
    text: str
    voice_id: str | None
    audio_url: str | None
    duration: float | None
    status: MediaStatus
    model_id: UUID | None
    task_id: UUID | None
    error: str | None
    # P2 差异化配音
    character_id: UUID | None = None
    emotion: str | None = None
    instruct_text: str | None = None
    line_index: int | None = None
    is_narration: bool = False
    created_at: datetime
    updated_at: datetime


class VoiceGenerateResp(BaseModel):
    """配音生成接口返回：VoiceLine + 关联任务。"""
    voiceline: VoiceLineOut
    task: TaskOut


class VoiceBatchGenerateResp(BaseModel):
    """批量配音重跑接口返回：父任务 + 子任务数 + 子任务 ID 列表。"""
    task: TaskOut
    total: int
    sub_task_ids: list[str]


class SubtitleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    segment_id: UUID
    text: str
    start_ms: int
    end_ms: int
    created_at: datetime
    updated_at: datetime


class SubtitleUpdate(BaseModel):
    text: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
