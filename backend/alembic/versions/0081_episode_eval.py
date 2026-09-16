"""episode_eval 表 + task_type 新增 evaluate_episode + eval_status 枚举（P0-1 成片评估）。

Revision ID: 0081
Revises: 0080
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0081_episode_eval"
down_revision: Union[str, None] = "0080_generate_shot_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "episode_eval",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("episode_id", sa.Uuid(), sa.ForeignKey("episode.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("scores", JSONB(), nullable=False, server_default="{}"),
        sa.Column("rule_scores", JSONB(), nullable=False, server_default="{}"),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("suggestions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Enum("running", "succeeded", "failed", name="eval_status"),
                  nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_episode_eval_episode_created", "episode_eval", ["episode_id", "created_at"])
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'evaluate_episode'")


def downgrade() -> None:
    op.drop_index("ix_episode_eval_episode_created", table_name="episode_eval")
    op.drop_table("episode_eval")
    op.execute("DROP TYPE IF EXISTS eval_status")
