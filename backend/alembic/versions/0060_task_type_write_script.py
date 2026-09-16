"""task_type 枚举新增 write_script（分集剧本写作任务，逐集生成完整剧本并追加为项目幕）。

Revision ID: 0060
Revises: 0059
"""
from typing import Union
from alembic import op

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'write_script'")


def downgrade() -> None:
    pass
