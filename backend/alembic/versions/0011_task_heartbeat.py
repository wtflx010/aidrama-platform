"""Add last_heartbeat_at to task for stuck-task detection.

Revision ID: 0011
Revises: 0010
"""
from typing import Union
from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 帮助回收任务按心跳扫描
    op.create_index("ix_task_status_heartbeat", "task", ["status", "last_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_task_status_heartbeat", table_name="task")
    op.drop_column("task", "last_heartbeat_at")
