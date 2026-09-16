"""task_type 枚举新增 batch_asset_covers（批量资产生成封面，P7）。

Revision ID: 0023
Revises: 0022
"""
from typing import Union
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'batch_asset_covers'")


def downgrade() -> None:
    pass
