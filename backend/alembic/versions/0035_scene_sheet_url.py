"""场景多视角：asset 表新增 scene_sheet_url 列；task_type 新增 generate_scene_multiview。

Revision ID: 0035
Revises: 0034
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("scene_sheet_url", sa.String(512), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_scene_multiview'"
        )


def downgrade() -> None:
    op.drop_column("asset", "scene_sheet_url")
    # PostgreSQL 不支持删除枚举值；回滚仅记录说明
