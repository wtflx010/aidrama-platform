from app.schemas.common import ErrorResp, IdResp, PageOut, Timestamps
from app.schemas.keyframe import KeyframeGenerate, KeyframeOut
from app.schemas.model_config import (
    ModelBrief,
    ModelCreate,
    ModelOut,
    ModelTestResult,
    ModelUpdate,
    SetDefaultBody,
    ToggleBody,
)
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.schemas.segment import ReorderBody, SegmentCreate, SegmentOut, SegmentUpdate
from app.schemas.task import GenerateResp, TaskOut
from app.schemas.video import VideoGenerate, VideoOut

__all__ = [
    "ErrorResp",
    "GenerateResp",
    "IdResp",
    "KeyframeGenerate",
    "KeyframeOut",
    "ModelBrief",
    "ModelCreate",
    "ModelOut",
    "ModelTestResult",
    "ModelUpdate",
    "PageOut",
    "ProjectCreate",
    "ProjectOut",
    "ProjectUpdate",
    "ReorderBody",
    "SegmentCreate",
    "SegmentOut",
    "SegmentUpdate",
    "SetDefaultBody",
    "TaskOut",
    "Timestamps",
    "ToggleBody",
    "VideoGenerate",
    "VideoOut",
]
