"""agent_role_config 时间戳列补 server_default（0047 建表遗漏，插入时 NULL 违反非空）。

Revision ID: 0048
Revises: 0047
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agent_role_config", "created_at",
        server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False,
    )
    op.alter_column(
        "agent_role_config", "updated_at",
        server_default=sa.func.now(), existing_type=sa.DateTime(timezone=True), existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column("agent_role_config", "created_at", server_default=None, existing_type=sa.DateTime(timezone=True))
    op.alter_column("agent_role_config", "updated_at", server_default=None, existing_type=sa.DateTime(timezone=True))
