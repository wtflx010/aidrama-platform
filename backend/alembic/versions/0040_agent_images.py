"""agent_message 增加 images 列（用户消息携带图片，视觉理解/按图生图参考用）。

Revision ID: 0040
Revises: 0039
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_message", sa.Column("images", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_message", "images")
