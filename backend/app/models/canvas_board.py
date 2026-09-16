"""画布文档表:导演画布(node/edge JSON document)持久化。

M1 生图工作台:文档含 nodes/edges 两个数组(与前端 @xyflow/react 序列化对齐);
version 递增 + snapshot(上一版)支持回滚;canvas_generate 任务写回生成结果时
不 bump version(避免任务回写污染手工保存的历史版本)。
"""
import uuid

from sqlalchemy import ForeignKey, Integer, String, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class CanvasBoard(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "canvas_board"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # {"nodes": [...], "edges": [...]}
    document: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 上一版 document 快照(rollback 用);首版保存前为 None
    snapshot: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_canvas_board_project_id", "project_id"),)
