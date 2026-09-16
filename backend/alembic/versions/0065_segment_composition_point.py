"""segment.composition_point 九宫格点位回写列

Revision ID: 0065
Revises: 0064
Create Date: 2026-08-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("composition_point", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("segment", "composition_point")
