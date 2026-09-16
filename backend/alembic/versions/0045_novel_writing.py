"""novel 表增加 AI 长篇小说写作字段：outline（大纲）+ writing_status（写作状态）。

Revision ID: 0045
Revises: 0044
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "novel",
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "novel",
        sa.Column(
            "writing_status",
            sa.String(length=16),
            nullable=False,
            server_default="none",
        ),
    )


def downgrade() -> None:
    op.drop_column("novel", "writing_status")
    op.drop_column("novel", "outline")
