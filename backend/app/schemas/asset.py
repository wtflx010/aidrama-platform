"""资产（角色/场景/道具）相关 schema。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.asset import AssetType
from app.models.media import MediaStatus
from app.schemas.task import TaskOut


class AssetCreate(BaseModel):
    type: AssetType
    name: str
    description: str | None = None
    project_id: UUID | None = None  # 由路径参数提供，body 中可省略


class AssetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    # 2026-08-11：系统生成的生图提示词（扩写描述）前端展示 + 编辑
    expanded_description: str | None = None
    # 2026-08-18：场景多视角机位组（POV 六格渲染格式 list[{"name","view_text"}]）
    scene_shots: list | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    # 2026-08-22 全局资产库：资产绑定的项目 id 列表（含归属项目），前端展示「绑定项目」信息
    project_ids: list[UUID] = []
    type: AssetType
    name: str
    description: str | None
    cover_url: str | None
    character_sheet_url: str | None
    scene_sheet_url: str | None
    scene_shots: list = []
    four_view_urls: list
    states: list
    reference_images: list
    art_versions: list
    expanded_description: str | None
    voice_profile: dict = {}
    # 方案A：资产生成时的项目生效风格指纹（style_id / style_prompt）
    style_fingerprint: dict = {}
    model_id: UUID | None
    task_id: UUID | None
    status: MediaStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


class VoiceProfileUpdate(BaseModel):
    """角色声线档案更新（部分更新，合并非覆盖）。"""
    gender: str | None = None
    age_group: str | None = None
    timbre_tags: list[str] | None = None
    reference_audio_url: str | None = None
    reference_audio_text: str | None = None
    default_emotion: str | None = None
    voice_description: str | None = None


class VoiceProfileOut(BaseModel):
    """角色声线档案。"""
    gender: str | None = None
    age_group: str | None = None
    timbre_tags: list = []
    reference_audio_url: str | None = None
    reference_audio_text: str | None = None
    default_emotion: str | None = None
    voice_description: str | None = None


class RecommendVoiceResp(BaseModel):
    """LLM 推荐声线返回。"""
    asset_id: UUID
    voice_profile: dict
    warnings: list[str] = []  # 声线-视觉一致性警告（不阻断）


class NarratorProfileUpdate(BaseModel):
    """项目旁白声线更新。"""
    gender: str | None = None
    age_group: str | None = None
    timbre_tags: list[str] | None = None
    reference_audio_url: str | None = None
    reference_audio_text: str | None = None
    default_emotion: str | None = None
    voice_description: str | None = None


class NarratorProfileOut(BaseModel):
    """项目旁白声线。"""
    narrator_profile: dict


class ExpandDescBody(BaseModel):
    model_id: UUID | None = None


class ProjectBindBody(BaseModel):
    """资产/音频绑定到项目。"""
    project_id: UUID


class ExpandDescOut(BaseModel):
    expanded_description: str


class AssetGenerateResp(BaseModel):
    """资产生成接口返回：资产对象 + 关联任务。"""
    asset: AssetOut
    task: TaskOut
