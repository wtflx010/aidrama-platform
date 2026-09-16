"""Remove task.cost_credits + drop global asset support.

Changes:
1. Drop task.cost_credits column (积分系统已移除，系统为个人使用).
2. Asset.project_id 改为 NOT NULL（资产跟项目，不做全局复用）。
   - 先删除残留的全局资产（project_id IS NULL 的行）。
   - 删除 ix_asset_global_unique partial index（全局库唯一约束不再需要）。
   - 将 ix_asset_project_unique partial index 重建为普通 unique index
     （project_id 既已 NOT NULL，partial WHERE 子句多余）。
   - ALTER COLUMN project_id SET NOT NULL。

Revision ID: 0008
Revises: 0007
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 移除 task.cost_credits
    op.drop_column("task", "cost_credits")

    # 2) 资产简化为项目级
    # 2a) 删除残留的全局资产（project_id IS NULL）
    op.execute("DELETE FROM asset WHERE project_id IS NULL")
    # 2b) 删除全局库 partial unique index
    op.drop_index("ix_asset_global_unique", table_name="asset")
    # 2c) 将项目级 partial unique index 重建为普通 unique index（project_id NOT NULL 后 partial 无意义）
    op.drop_index("ix_asset_project_unique", table_name="asset")
    op.create_index(
        "ix_asset_project_unique",
        "asset",
        [sa.text("lower(name)"), sa.text("type"), sa.text("project_id")],
        unique=True,
    )
    # 2d) project_id SET NOT NULL
    op.alter_column(
        "asset",
        "project_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )


def downgrade() -> None:
    # 回滚：恢复 project_id nullable + 重建两个 partial unique index + 恢复 cost_credits
    op.alter_column(
        "asset",
        "project_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_index("ix_asset_project_unique", table_name="asset")
    op.create_index(
        "ix_asset_project_unique",
        "asset",
        [sa.text("lower(name)"), sa.text("type"), sa.text("project_id")],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index(
        "ix_asset_global_unique",
        "asset",
        [sa.text("lower(name)"), sa.text("type")],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.add_column(
        "task",
        sa.Column("cost_credits", sa.Integer, nullable=False, server_default="0"),
    )
