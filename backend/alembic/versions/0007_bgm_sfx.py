"""Add bgm_track + sfx_clip tables for P3 BGM/SFX.

Revision ID: 0007
Revises: 0006
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bgm_track",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("episode.id", ondelete="CASCADE"),
        ),
        sa.Column("emotion", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("audio_url", sa.String(512)),
        sa.Column("duration", sa.Float, nullable=False, server_default="0"),
        sa.Column("volume", sa.Float, nullable=False, server_default="0.15"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text),
        sa.Column("source", sa.String(32), nullable=False, server_default="musicgen"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_bgm_project_id", "bgm_track", ["project_id"])

    op.create_table(
        "sfx_clip",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "segment_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("segment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sfx_type", sa.String(64), nullable=False),
        sa.Column("sfx_name", sa.String(128), nullable=False),
        sa.Column("audio_url", sa.String(512)),
        sa.Column("start_time", sa.Float, nullable=False, server_default="0"),
        sa.Column("duration", sa.Float, nullable=False, server_default="0"),
        sa.Column("volume", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text),
        sa.Column("source", sa.String(32), nullable=False, server_default="freesound"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sfx_segment_id", "sfx_clip", ["segment_id"])


def downgrade() -> None:
    op.drop_index("ix_sfx_segment_id", table_name="sfx_clip")
    op.drop_table("sfx_clip")
    op.drop_index("ix_bgm_project_id", table_name="bgm_track")
    op.drop_table("bgm_track")
