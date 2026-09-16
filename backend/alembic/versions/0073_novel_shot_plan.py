# -*- coding: utf-8 -*-
"""
novel 新增 shot_plan 列（写剧本后预生成的分镜预览，确认式生成项目用，不做项目）。

Revision ID: 0073
Revises: 0072
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("novel", sa.Column("shot_plan", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("novel", "shot_plan")
