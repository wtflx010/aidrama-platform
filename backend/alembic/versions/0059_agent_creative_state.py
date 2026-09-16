"""新增 agent_creative_state 表（创作状态卡：会话各创作角色当前版本，P1 状态层）。

Revision ID: 0059
Revises: 0058
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_creative_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("role_key", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_agent_creative_state_session_role",
        "agent_creative_state",
        ["session_id", "role_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_creative_state_session_role", table_name="agent_creative_state")
    op.drop_table("agent_creative_state")
