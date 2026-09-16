"""agent_session 新增 tool_whitelist 列（P9 会话级工具白名单：NULL=全部可用，[]=仅白名单内）。

Revision ID: 0054
Revises: 0053
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column("tool_whitelist", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_session", "tool_whitelist")
