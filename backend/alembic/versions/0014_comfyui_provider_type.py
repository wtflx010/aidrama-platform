"""provider_type 枚举新增 comfyui 值。

Revision ID: 0014
Revises: 0013
"""
from typing import Union
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL 12+ 支持在事务中 ADD VALUE；迁移内不写入该枚举值，安全。
    op.execute("ALTER TYPE provider_type ADD VALUE IF NOT EXISTS 'comfyui'")


def downgrade() -> None:
    # PG 无法直接删除枚举值（除非重建类型并重写数据）。
    # comfyui 行数据存在时不可安全回退，留空由人工处理。
    pass
