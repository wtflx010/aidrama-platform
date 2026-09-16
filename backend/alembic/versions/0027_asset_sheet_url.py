"""P7.9 角色单画布整合图：asset 表新增 sheet_url 列。

单画布整合图（2x2：特写/正面/侧面/背面）作为多角度人设参考，关键帧/视频生成时
与景别分图双参考，让模型看到角色多角度特征，任意镜头角度不换脸。

Revision ID: 0027
Revises: 0026
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("sheet_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "sheet_url")
