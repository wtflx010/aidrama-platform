"""agent_goal 表（创作目标 Goal 工作流：设定目标后 AI 多轮自动推进，每轮自评）。

Revision ID: 0044
Revises: 0043
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_goal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("criteria", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("progress_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_session.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("agent_goal")
