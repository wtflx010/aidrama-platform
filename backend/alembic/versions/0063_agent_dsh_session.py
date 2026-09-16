"""新增 agent_dsh_session 表：ai漫剧会话 → dsh 会话映射（Phase 4 代理转发）。

Revision ID: 0063
Revises: 0062
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_dsh_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dsh_session_id", sa.String(64), nullable=False),
        sa.Column("cwd", sa.String(512), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["agent_session.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", name="uq_agent_dsh_session_session_id"),
        sa.UniqueConstraint("dsh_session_id", name="uq_agent_dsh_session_dsh_session_id"),
    )
    op.create_index("ix_agent_dsh_session_session_id", "agent_dsh_session", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_dsh_session_session_id", table_name="agent_dsh_session")
    op.drop_table("agent_dsh_session")