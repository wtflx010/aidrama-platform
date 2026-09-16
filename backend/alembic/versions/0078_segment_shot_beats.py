"""segment 新增 shot_beats 列（分镜内多镜头运镜节拍 JSONB）。

Revision ID: 0078_segment_shot_beats
Revises: 0077_video_draft_regen
Create Date: 2026-08-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0078_segment_shot_beats"
down_revision = "0077_video_draft_regen"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "segment",
        sa.Column("shot_beats", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade():
    op.drop_column("segment", "shot_beats")
