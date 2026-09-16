"""Add novel table + new TaskType enum values for P3.

Revision ID: 0006
Revises: 0005
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "novel",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("chapters_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("word_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("analysis_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("analysis_result", JSONB),
        sa.Column("error", sa.Text),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_novel_project_id", "novel", ["project_id"])

    # TaskType 新增枚举值
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'analyze_novel'")
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'adapt_script'")


def downgrade() -> None:
    op.drop_index("ix_novel_project_id", table_name="novel")
    op.drop_table("novel")
    # 注意：PostgreSQL 不支持直接移除 enum 值，需重建类型
