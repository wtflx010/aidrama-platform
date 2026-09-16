"""segment 新增 gen_params 列（可配置视频生成参数 JSONB）。

Revision ID: 0070
Revises: 0069
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0070"
down_revision: Union[str, None] = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("gen_params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("segment", "gen_params")
