"""segment 表新增提示词增强缓存字段（P4 提示词精细化管线）。

Revision ID: 0015
Revises: 0014
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("enhanced_prompt", sa.Text()))
    op.add_column("segment", sa.Column("enhanced_negative_prompt", sa.Text()))


def downgrade() -> None:
    op.drop_column("segment", "enhanced_negative_prompt")
    op.drop_column("segment", "enhanced_prompt")
