"""task_type 枚举新增 write_novel（AI 长篇小说写作任务）。

Revision ID: 0046
Revises: 0045
"""
from typing import Union
from alembic import op

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'write_novel'")


def downgrade() -> None:
    pass
