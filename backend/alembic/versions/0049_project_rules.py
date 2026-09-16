"""project 表新增 rules 列（项目级创作规则）。

Revision ID: 0049
Revises: 0048
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project", sa.Column("rules", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project", "rules")
