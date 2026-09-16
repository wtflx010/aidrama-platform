"""Add batch_voice task_type for P2 voice batch regenerate.

Revision ID: 0010
Revises: 0009
"""
from typing import Union
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 的 ALTER TYPE ADD VALUE 不能在事务内执行，用 autocommit_block 跳出事务
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'batch_voice'")


def downgrade() -> None:
    # PG 不支持直接删除 enum 值，downgrade 需重建枚举类型，这里保留值不删
    pass
