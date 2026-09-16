"""资产全局库改造：
1) asset.project_id 改为 nullable（ondelete CASCADE → SET NULL）——资产可脱离项目存活
2) 新建 project_asset 绑定表（asset ↔ project 多对多），记录资产被哪些项目使用
3) 存量迁移：资产原本归属的项目写入绑定表。

Revision ID: 0068
Revises: 0067
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.create_table(
        "project_asset",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "project_id", name="uq_project_asset"),
    )
    # asset.project_id 外键改为 SET NULL + 列可空
    op.drop_constraint("asset_project_id_fkey", "asset", type_="foreignkey")
    op.create_foreign_key(
        "asset_project_id_fkey", "asset", "project",
        ["project_id"], ["id"], ondelete="SET NULL",
    )
    op.alter_column("asset", "project_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    # 存量：原有归属项目写入绑定表
    op.execute(
        "INSERT INTO project_asset (id, asset_id, project_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), id, project_id, now(), now() FROM asset WHERE project_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("project_asset")
    op.drop_constraint("asset_project_id_fkey", "asset", type_="foreignkey")
    op.create_foreign_key(
        "asset_project_id_fkey", "asset", "project",
        ["project_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("asset", "project_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
