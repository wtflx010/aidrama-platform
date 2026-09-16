"""task_type 枚举新增 adapt_continuation（P6 章节续接追加）。

Revision ID: 0022
Revises: 0021
"""
from typing import Union
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'adapt_continuation'")


def downgrade() -> None:
    pass
