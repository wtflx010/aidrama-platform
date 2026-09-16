from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SegmentCreate(BaseModel):
    index: int | None = None
    shot_type: str | None = None
    camera: str | None = None
    description: str | None = None
    dialogue: str | None = None
    narration: str | None = None
    duration: float = 5.0
    # 2026-08-28 分镜内多镜头运镜节拍：[{start_sec, end_sec, shot_type, camera, content}]
    shot_beats: list[dict] | None = None


class SegmentUpdate(BaseModel):
    index: int | None = None
    gen_params: dict | None = None
    title: str | None = None
    shot_type: str | None = None
    camera: str | None = None
    description: str | None = None
    dialogue: str | None = None
    narration: str | None = None
    duration: float | None = None
    # 2026-08-28 分镜内多镜头运镜节拍：[{start_sec, end_sec, shot_type, camera, content}]
    shot_beats: list[dict] | None = None
    locked: bool | None = None
    character_ids: list[UUID] | None = None
    scene_id: UUID | None = None
    prop_ids: list[UUID] | None = None
    composition_point: str | None = None


class SegmentAssetBinding(BaseModel):
    """分镜资产绑定专用请求体。"""
    character_ids: list[UUID] | None = None
    scene_id: UUID | None = None
    prop_ids: list[UUID] | None = None


class SegmentAudioBind(BaseModel):
    """分镜音频绑定请求体：把全局 BGM/SFX 绑定到分镜。

    - BGM → 所属幕（BgmTrack.episode_id）——成片导出按幕混音
    - SFX → 分镜（SfxClip.segment_id）——成片导出按分镜混音
    - clear_* = True 表示解除当前绑定（不指定）
    """
    bgm_id: UUID | None = None
    sfx_id: UUID | None = None
    clear_bgm: bool = False
    clear_sfx: bool = False


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    episode_id: UUID
    index: int
    title: str | None
    gen_params: dict = {}
    shot_type: str | None
    camera: str | None
    description: str | None
    dialogue: str | None
    narration: str | None
    duration: float
    character_ids: list[Any]
    scene_id: str | None
    prop_ids: list[Any]
    locked: bool
    composition_point: str | None = None
    # P2 差异化配音：结构化对白 + 镜级情绪
    dialogue_lines: list = []
    emotion: str | None = None
    # 2026-08-10：动作序列标记（打斗/动作段连续分镜同一标记，如 as_1）
    action_sequence: str | None = None
    # 2026-08-28：分镜内多镜头运镜节拍
    shot_beats: list = []
    created_at: datetime
    updated_at: datetime


class ReorderBody(BaseModel):
    ordered_ids: list[UUID]


class SegmentEnhanceIn(BaseModel):
    """画布扩写入参:prompt 为空时用分镜描述;target 决定增强侧重(image/video)。

    lang：结构化正文语言（zh=简体中文六段式 / en=英文，默认 zh）。仅 video 生效；
    生成链路（worker）固定 en，中文结构化只影响展示/保存。
    """

    prompt: str | None = None
    target: str = "image"
    force: bool = False
    lang: str = "zh"
