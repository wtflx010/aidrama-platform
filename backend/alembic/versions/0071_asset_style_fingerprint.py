"""asset 新增 style_fingerprint 列（方案A 风格指纹：生成时项目生效风格）。

Revision ID: 0071
Revises: 0070
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0071"
down_revision: Union[str, None] = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asset",
        sa.Column("style_fingerprint", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("asset", "style_fingerprint")
