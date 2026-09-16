"""video upsale: video_clip 加超分标记列 + task_type 新增超分任务类型。

Revision ID: 0075_video_upscale
Revises: 0074_project_video_params
Create Date: 2026-08-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0075_video_upscale"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade():
    # task_type 枚举扩展：超分单镜 / 超分批量（PG12+ 支持事务内 ADD VALUE，Alembic 迁移默认在事务中）
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'upscale_video'")
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'batch_upscale_videos'")
    # video_clip 超分标记
    op.add_column(
        "video_clip",
        sa.Column("is_upscaled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("video_clip", sa.Column("upscale_of_id", sa.UUID(), nullable=True))
    op.create_index("ix_video_clip_upscale_of", "video_clip", ["upscale_of_id"])


def downgrade():
    op.drop_index("ix_video_clip_upscale_of", table_name="video_clip")
    op.drop_column("video_clip", "upscale_of_id")
    op.drop_column("video_clip", "is_upscaled")
    # 枚举值删除需在 PG 原生事务外执行，降级仅移除列（枚举残留无害）
