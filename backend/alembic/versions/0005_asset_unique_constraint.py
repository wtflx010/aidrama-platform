"""Add partial unique indexes on asset (lower(name), type, project_id)

Problem: _find_or_create_asset does "SELECT then INSERT" non-atomically. Two
concurrent requests can both miss the lookup and both insert, producing
duplicate assets that break the cross-project consistency design.

Fix: two partial unique indexes:
  - Global assets (project_id IS NULL): unique on (lower(name), type)
  - Project assets (project_id IS NOT NULL): unique on (lower(name), type, project_id)

PostgreSQL NULLs are distinct in UNIQUE, so a single unique index on
(lower(name), type, project_id) would NOT prevent duplicate global assets
(both with project_id=NULL). Partial indexes solve this cleanly.

The old non-unique index ix_asset_name_type is dropped (redundant with the
new partial unique indexes).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 旧的非唯一索引已冗余，删除
    op.drop_index("ix_asset_name_type", table_name="asset")
    # 全局库资产唯一：(lower(name), type) 在 project_id IS NULL 时唯一
    op.create_index(
        "ix_asset_global_unique",
        "asset",
        [text("lower(name)"), text("type")],
        unique=True,
        postgresql_where=text("project_id IS NULL"),
    )
    # 项目级资产唯一：(lower(name), type, project_id) 在 project_id IS NOT NULL 时唯一
    op.create_index(
        "ix_asset_project_unique",
        "asset",
        [text("lower(name)"), text("type"), text("project_id")],
        unique=True,
        postgresql_where=text("project_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_asset_project_unique", table_name="asset")
    op.drop_index("ix_asset_global_unique", table_name="asset")
    # 恢复旧的非唯一索引
    op.create_index(
        "ix_asset_name_type",
        "asset",
        [text("lower(name)"), text("type")],
    )
