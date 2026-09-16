"""canvas_board 画布文档表 + canvas_generate 任务类型(生图工作台 M1)。

Revision ID: 0064
Revises: 0063
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canvas_board",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canvas_board_project_id", "canvas_board", ["project_id"])
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'canvas_generate'")


def downgrade() -> None:
    op.drop_index("ix_canvas_board_project_id", table_name="canvas_board")
    op.drop_table("canvas_board")
    # task_type 枚举值不回退(PostgreSQL ALTER TYPE 移除值成本高,现网无需)
