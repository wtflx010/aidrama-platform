"""P2: multi-episode consistency - asset/task project_id nullable for global asset library

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Asset.project_id 改 nullable（NULL=全局库资产，跨项目复用）
    op.alter_column("asset", "project_id", nullable=True)

    # 2) 替换索引：去掉 project_id 维度，新增按 type 与 (lower(name),type) 的索引
    op.drop_index("ix_asset_project_type", table_name="asset")
    op.create_index("ix_asset_type", "asset", ["type"])
    op.create_index(
        "ix_asset_name_type", "asset",
        [sa.text("lower(name)"), sa.text("type")],
    )

    # 3) Task.project_id 改 nullable（全局资产生成任务无处挂载时的容错）
    op.alter_column("task", "project_id", nullable=True)


def downgrade() -> None:
    # 回退前需把 NULL project_id 的 task/asset 处理掉
    op.execute(
        "UPDATE task SET project_id = (SELECT id FROM project LIMIT 1) "
        "WHERE project_id IS NULL AND EXISTS (SELECT 1 FROM project)"
    )
    op.alter_column("task", "project_id", nullable=False)
    op.drop_index("ix_asset_name_type", table_name="asset")
    op.drop_index("ix_asset_type", table_name="asset")
    op.create_index("ix_asset_project_type", "asset", ["project_id", "type"])
    op.alter_column("asset", "project_id", nullable=False)
