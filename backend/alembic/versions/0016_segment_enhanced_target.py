"""segment 表新增 enhanced_target 缓存目标标记（P4 提示词精细化管线）。

image/video 两种增强模板分开缓存，避免互相覆盖导致反复调 LLM。

Revision ID: 0016
Revises: 0015
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("enhanced_target", sa.String(16)))


def downgrade() -> None:
    op.drop_column("segment", "enhanced_target")
