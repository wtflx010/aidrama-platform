"""agent_session 增加 summary 列（长对话历史摘要缓存，上下文裁剪用）。

Revision ID: 0039
Revises: 0038
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_session", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_session", "summary")
