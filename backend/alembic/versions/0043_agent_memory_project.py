"""agent_memory 增加 project_id 列（项目级记忆：仅当 @ 引用该项目时注入）。

Revision ID: 0043
Revises: 0042
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_memory",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_memory_project", "agent_memory", "project",
        ["project_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_memory_project", "agent_memory", type_="foreignkey")
    op.drop_column("agent_memory", "project_id")
