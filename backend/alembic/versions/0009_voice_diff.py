"""Add voice differentiation fields for P2.

- Asset.voice_profile JSONB（角色声线档案）
- Segment.dialogue_lines JSONB + emotion（结构化对白 + 镜级氛围）
- VoiceLine.character_id/emotion/instruct_text/line_index/is_narration
- Project.narrator_profile JSONB（项目级旁白声线）

Revision ID: 0009
Revises: 0008
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Asset：角色声线档案
    op.add_column(
        "asset",
        sa.Column(
            "voice_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    # Segment：结构化对白 + 镜级氛围
    op.add_column(
        "segment",
        sa.Column(
            "dialogue_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "segment",
        sa.Column("emotion", sa.String(32), nullable=True),
    )

    # VoiceLine：说话人 + 情绪 + 指令 + 顺序 + 旁白标记
    op.add_column(
        "voice_line",
        sa.Column(
            "character_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asset.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "voice_line",
        sa.Column("emotion", sa.String(32), nullable=True),
    )
    op.add_column(
        "voice_line",
        sa.Column("instruct_text", sa.Text, nullable=True),
    )
    op.add_column(
        "voice_line",
        sa.Column("line_index", sa.Integer, nullable=True),
    )
    op.add_column(
        "voice_line",
        sa.Column(
            "is_narration",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_voice_line_segment_index", "voice_line", ["segment_id", "line_index"]
    )

    # Project：项目级旁白声线
    op.add_column(
        "project",
        sa.Column(
            "narrator_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("project", "narrator_profile")
    op.drop_index("ix_voice_line_segment_index", table_name="voice_line")
    op.drop_column("voice_line", "is_narration")
    op.drop_column("voice_line", "line_index")
    op.drop_column("voice_line", "instruct_text")
    op.drop_column("voice_line", "emotion")
    op.drop_column("voice_line", "character_id")
    op.drop_column("segment", "emotion")
    op.drop_column("segment", "dialogue_lines")
    op.drop_column("asset", "voice_profile")
