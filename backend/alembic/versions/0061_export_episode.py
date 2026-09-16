"""新增 task_type 枚举值 export_episode（剧集导出，按幕导出单集成片）。

Revision ID: 0061
Revises: 0060
"""
from typing import Union
from alembic import op


revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 的 ALTER TYPE ADD VALUE 不能在事务内执行，用 autocommit_block 跳出事务
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'export_episode'")


def downgrade() -> None:
    # PG 不支持直接删除 enum 值，downgrade 保留
    pass
