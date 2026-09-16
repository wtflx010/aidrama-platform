"""项目页签「一集一条连续长片」请求/响应模型（2026-09）。

与画布导演台(director_generate)不同：本入口按整集分段连续出整片，不走画布。
"""
import uuid

from pydantic import BaseModel


class EpisodeFilmGenerate(BaseModel):
    """项目页签连续长片请求体：缺省 segment_ids=整集全部分镜（按 index 排序）。"""

    model_id: uuid.UUID | None = None
    config: dict | None = None  # task_type/ratio/res/fps/steps/sampler/scheduler/cfg/seed/shift_video/shift_audio/context_enabled/context_frames
    segment_ids: list[uuid.UUID] | None = None  # 缺省=整集全部；可指定连续子集
    global_prompt: str | None = None


class EpisodeFilmGenerateOut(BaseModel):
    task_id: uuid.UUID
    episode_id: uuid.UUID
    segment_count: int
    total_frames: int
    message: str | None = None
