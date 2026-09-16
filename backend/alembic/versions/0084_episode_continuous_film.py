"""episode 新增整片连续长片字段 + task_type 新增 project_director（项目页签连续长片）。

Revision ID: 0084
Revises: 0083
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0084_episode_continuous_film"
down_revision: Union[str, None] = "0083_rework_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'project_director'")
    op.add_column("episode", sa.Column("continuous_film_url", sa.String(512), nullable=True))
    op.add_column("episode", sa.Column("continuous_film_duration", sa.Float, nullable=True))
    op.add_column("episode", sa.Column("continuous_film_meta", JSONB, nullable=True))
    op.add_column("episode", sa.Column("continuous_film_status", sa.String(16), nullable=False, server_default="none"))


def downgrade() -> None:
    op.drop_column("episode", "continuous_film_status")
    op.drop_column("episode", "continuous_film_meta")
    op.drop_column("episode", "continuous_film_duration")
    op.drop_column("episode", "continuous_film_url")
