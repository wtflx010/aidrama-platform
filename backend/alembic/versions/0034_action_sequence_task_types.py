"""P 白模故事版：task_type 枚举新增动作序列任务类型。

Revision ID: 0034
Revises: 0033
"""
from typing import Union
from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_action_sequence_template'"
        )
        op.execute(
            "ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'compose_action_sequence'"
        )


def downgrade() -> None:
    # PostgreSQL 不支持删除枚举值；回滚仅记录说明
    pass
