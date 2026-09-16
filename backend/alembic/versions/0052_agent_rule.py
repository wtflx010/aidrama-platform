"""新增 agent_rule 表（规则注入，对齐 TraeWork Rules：全局/项目级规则自动注入对话）。

Revision ID: 0052
Revises: 0051
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_rule",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="global"),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_rule")
