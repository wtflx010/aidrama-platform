"""新增 agent_schedule 表（定时自动化任务，对齐 Hermes/OpenClaw cron 调度）。

Revision ID: 0053
Revises: 0052
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_schedule",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("cron_expr", sa.String(50), nullable=False),
        sa.Column("action_type", sa.String(20), nullable=False, server_default="prompt"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("system_action", sa.String(50), nullable=False, server_default=""),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_schedule")
