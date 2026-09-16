"""novel 新增 poster_url 列（剧本海报：写剧本时由文生图自动生成）。

Revision ID: 0072
Revises: 0071
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("novel", sa.Column("poster_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("novel", "poster_url")
