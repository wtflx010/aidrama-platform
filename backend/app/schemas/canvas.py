"""画布(生图工作台)Schemas。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CanvasBoardCreate(BaseModel):
    name: str = Field(default="导演画布", max_length=120)
    project_id: UUID | None = None


class CanvasBoardSave(BaseModel):
    document: dict
    name: str | None = None


class CanvasSegmentImport(BaseModel):
    segment_id: UUID
    name: str | None = None


class CanvasSegmentsImport(BaseModel):
    """多分镜合并导入(M3 导演层):一次把多个分镜(可跨幕)落成一张画布。"""

    segment_ids: list[UUID]
    name: str | None = None


class CanvasBoardGenerate(BaseModel):
    node_ids: list[str]
    model_id: UUID | None = None
    ratio: str | None = None
    # M2:image=关键帧(默认)| video=镜头视频(R2V 直接出片,复用 video_service 链路)
    kind: str = "image"


class CanvasNodeGenerateOut(BaseModel):
    node_id: str
    task_id: UUID | None = None
    keyframe_id: UUID | None = None
    error: str | None = None


class CanvasBoardGenerateOut(BaseModel):
    task_id: UUID
    nodes: list[CanvasNodeGenerateOut]


class CanvasBoardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID | None
    name: str
    document: dict
    version: int
    created_at: datetime
    updated_at: datetime
class DirectorShot(BaseModel):
    """导演台分段脚本：一行=一段镜（顺序=时间顺序）。"""

    node_id: str
    prompt: str | None = None  # 缺省由后端 LLM 视频增强补全
    duration_sec: float | None = None  # None/<=0 → 后端按分镜时长换算
    from_prev: bool = True  # 引用上段（段间引导，需 config.context_enabled）


class DirectorGenerate(BaseModel):
    """画布导演台模式请求体（与 generate 的 node_ids 语义不同：
    这里是『一段连续影片』的镜头清单，不是批量分别生成）。
    """

    node_ids: list[str]
    config: dict | None = None  # task_type/ratio/res/fps/steps/sampler/scheduler/shift_video/shift_audio/context_enabled/context_frames/refine
    shots: list[DirectorShot] | None = None  # 缺省则按 node_ids 顺序自动生成


class DirectorGenerateOut(BaseModel):
    task_id: UUID
    board_id: UUID
    node_ids: list[str]
    message: str | None = None

# ── 画布生成方案体系（2026-08-29）──────────────────────────


class SchemeMeta(BaseModel):
    """方案元数据（GET /canvas/schemes 用）：前端渲染方案菜单。"""

    key: str
    label: str
    description: str = ""
    input_kind: str = "node-batch"  # node-batch | director-segments | ...


class SchemeGenerate(BaseModel):
    """统一方案执行请求体：scheme 分发 + 通用字段 + 方案私有 config。"""

    scheme: str
    node_ids: list[str] = Field(default_factory=list)
    kind: str = "image"  # single 方案：image=关键帧 | video=镜头视频
    model_id: UUID | None = None
    ratio: str | None = None
    config: dict | None = None  # 方案私有参数（director 的 ratio/res/context 等）
    shots: list[DirectorShot] | None = None  # director 分段脚本


class SchemeGenerateOut(BaseModel):
    task_id: UUID
    board_id: UUID
    scheme: str
    node_ids: list[str]
    nodes: list[dict] = Field(default_factory=list)  # single: 逐节点结果
    message: str | None = None


