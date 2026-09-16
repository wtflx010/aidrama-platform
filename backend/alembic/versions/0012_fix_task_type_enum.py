"""Fix missing task_type enum values: generate_bgm, generate_sfx.

0007_bgm_sfx 创建了表但漏了给 task_type 枚举补值，导致 BGM/SFX 任务
INSERT 时 PG 报 invalid input value for enum task_type。此处补齐。

Revision ID: 0012
Revises: 0011
"""
from typing import Union
from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 的 ALTER TYPE ADD VALUE 不能在事务内执行，用 autocommit_block 跳出事务
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_bgm'")
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_sfx'")


def downgrade() -> None:
    # PG 不支持直接删除 enum 值，downgrade 保留
    pass
