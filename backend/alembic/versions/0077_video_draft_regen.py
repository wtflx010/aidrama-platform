"""video draft regenerate: video_draft 加生成档位与二采来源列。

Revision ID: 0077_video_draft_regen
Revises: 0076_drop_system_metric_sample
Create Date: 2026-08-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0077_video_draft_regen"
down_revision = "0076_drop_system_metric_sample"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("video_draft", sa.Column("resolution", sa.String(16), nullable=True))
    op.add_column(
        "video_draft",
        sa.Column("base_draft_id", sa.UUID(), sa.ForeignKey("video_draft.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_video_draft_base", "video_draft", ["base_draft_id"])


def downgrade():
    op.drop_index("ix_video_draft_base", table_name="video_draft")
    op.drop_column("video_draft", "base_draft_id")
    op.drop_column("video_draft", "resolution")
