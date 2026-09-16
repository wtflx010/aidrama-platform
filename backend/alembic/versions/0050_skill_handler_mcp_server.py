"""agent_skill 加 handler 列 + 新增 agent_mcp_server 表（MCP 服务器配置）。

Revision ID: 0050
Revises: 0049
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_skill", sa.Column("handler", sa.String(100), nullable=True))
    op.create_table(
        "agent_mcp_server",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("command", sa.String(500), nullable=False),
        sa.Column("args", JSONB(), nullable=True),
        sa.Column("env", JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_mcp_server")
    op.drop_column("agent_skill", "handler")
