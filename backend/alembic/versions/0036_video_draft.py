"""AI 视频草稿：新建 video_draft 表；task_type 新增 generate_video_draft。

Revision ID: 0036
Revises: 0035
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_draft",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=True),
        sa.Column("enhanced_prompt", sa.Text(), nullable=True),
        sa.Column("first_frame_url", sa.String(512), nullable=True),
        sa.Column("last_frame_url", sa.String(512), nullable=True),
        sa.Column("ref_image_urls", sa.JSON(), nullable=True),
        sa.Column("ref_video_urls", sa.JSON(), nullable=True),
        sa.Column("asset_refs", sa.JSON(), nullable=True),
        sa.Column("aspect_ratio", sa.String(16), nullable=False, server_default="16:9"),
        sa.Column("duration", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("video_url", sa.String(512), nullable=True),
        sa.Column("status", postgresql.ENUM("pending", "running", "succeeded", "failed",
                                             name="media_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("model.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_video_draft'"
        )


def downgrade() -> None:
    op.drop_table("video_draft")
    # PostgreSQL 不支持删除枚举值；回滚仅记录说明
