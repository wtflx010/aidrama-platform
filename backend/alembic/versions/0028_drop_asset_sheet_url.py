"""移除角色单画布整合图 sheet_url 列。

2026-08-06 用户决定不做拼接图：四视图回到独立生成（正面=封面、侧面/背面按封面
生成、特写=封面裁头部），整合图双参考方案废弃，删除 asset.sheet_url 列。

Revision ID: 0028
Revises: 0027
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("asset", "sheet_url")


def downgrade() -> None:
    op.add_column("asset", sa.Column("sheet_url", sa.String(512), nullable=True))
