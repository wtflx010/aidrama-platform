"""segment 表新增 title 列（分镜标题，2~6 字概要，LLM 生成或导入剧本时产出）。

Revision ID: 0067
Revises: 0066
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("title", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("segment", "title")
