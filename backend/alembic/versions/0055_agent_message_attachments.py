"""agent_message 新增 attachments 列（P9 多模态附件持久化：历史回放重新注入）。

Revision ID: 0055
Revises: 0054
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_message",
        sa.Column("attachments", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_message", "attachments")
