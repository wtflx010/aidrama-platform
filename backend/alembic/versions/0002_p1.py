"""P1: asset/voice_line/subtitle tables + task_type enum extension

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PG 的 ALTER TYPE ADD VALUE 不能在事务内执行，用 autocommit_block 跳出事务
    with op.get_context().autocommit_block():
        for v in [
            "generate_asset_cover",
            "generate_asset_fourview",
            "generate_voice",
            "batch_keyframes",
            "batch_videos",
        ]:
            op.execute(f"ALTER TYPE task_type ADD VALUE IF NOT EXISTS '{v}'")

    # 复用已存在的 media_status 枚举（0001 已建），create_type=False 防止重复创建
    media_status = postgresql.ENUM("pending", "running", "succeeded", "failed",
                                   name="media_status", create_type=False)
    asset_type = postgresql.ENUM("character", "scene", "prop", name="asset_type")

    # 资产表（角色 / 场景 / 道具统一）
    op.create_table(
        "asset",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", asset_type, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("cover_url", sa.String(512)),
        sa.Column("four_view_urls", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("states", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("reference_images", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("art_versions", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expanded_description", sa.Text()),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model.id")),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task.id")),
        sa.Column("status", media_status, nullable=False, server_default="pending"),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_asset_project_type", "asset", ["project_id", "type"])

    # 配音表
    op.create_table(
        "voice_line",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("segment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("voice_id", sa.String(64)),
        sa.Column("audio_url", sa.String(512)),
        sa.Column("duration", sa.Float),
        sa.Column("status", media_status, nullable=False, server_default="pending"),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model.id")),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task.id")),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_voice_line_segment", "voice_line", ["segment_id"])

    # 字幕表
    op.create_table(
        "subtitle",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("segment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer, nullable=False),
        sa.Column("end_ms", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_subtitle_segment", "subtitle", ["segment_id"])


def downgrade() -> None:
    op.drop_index("ix_subtitle_segment", table_name="subtitle")
    op.drop_table("subtitle")
    op.drop_index("ix_voice_line_segment", table_name="voice_line")
    op.drop_table("voice_line")
    op.drop_index("ix_asset_project_type", table_name="asset")
    op.drop_table("asset")
    sa.Enum(name="asset_type").drop(op.get_bind(), checkfirst=True)
    # task_type 新增的枚举值无法移除（PG 不支持 REMOVE VALUE），保留即可
