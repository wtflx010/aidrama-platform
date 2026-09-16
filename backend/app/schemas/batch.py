"""批量生成相关 schema。"""
from uuid import UUID

from pydantic import BaseModel

from app.schemas.task import TaskOut


class BatchBody(BaseModel):
    model_id: UUID | None = None
    # 2026-09-01 链式串行批量：None=自动（批量内存在 prev_tail 分镜时自动串行）、
    # True=强制串行逐镜续帧、False=强制并发（忽略 prev_tail）。
    chained: bool | None = None
    # 2026-09-02 批量弹窗「初始帧来源」：none=不强制（跟随分镜现有自动路由 资产→R2V/无资产→T2V）、
    # custom=手工上传首帧图（custom_first_frame_url 应用到所有分镜）、prev_tail=上一分镜尾帧（强制串行）。
    reference_src: str | None = None
    custom_first_frame_url: str | None = None
    # 批量「覆盖已生成视频」：True=删除并重新生成已有成片的分镜；False=跳过已有成片的分镜。
    overwrite: bool = False


class BatchResp(BaseModel):
    task: TaskOut
    total: int
    dispatched_ids: list[str]
