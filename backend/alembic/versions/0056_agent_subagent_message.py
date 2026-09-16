"""agent_subagent_message 表：子智能体独立会话历史（P9 ③，按主会话隔离）。

每个 (主会话 session_id, role_key) 保留「任务→产出」轮次，下次调用子智能体时注入最近历史。

Revision ID: 0056
Revises: 0055
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_subagent_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("role_key", sa.String(50), nullable=False, index=True),
        sa.Column("role", sa.String(20), nullable=False),  # user（任务）/ assistant（产出）
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_agent_subagent_session_role",
        "agent_subagent_message",
        ["session_id", "role_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_subagent_session_role", table_name="agent_subagent_message")
    op.drop_table("agent_subagent_message")
