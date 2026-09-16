"""新增 agent_role_config 表（可配置创作角色/子智能体，T2）。

Revision ID: 0047
Revises: 0046
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_role_config",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role_key", sa.String(length=50), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("role_key"),
    )


def downgrade() -> None:
    op.drop_table("agent_role_config")
