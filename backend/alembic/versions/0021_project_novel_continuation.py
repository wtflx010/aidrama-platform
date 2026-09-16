"""project 表新增小说章节续接追加字段（P6）。

新增 2 个可空字段（不破坏现有数据）：
- source_novel_id UUID FK → novel.id (SET NULL)：项目来源小说
- processed_upto_chapter Integer：已改编到的章节号（续接断点，防乱序/重复追加）

Revision ID: 0021
Revises: 0020
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project", sa.Column("source_novel_id", sa.Uuid()))
    op.add_column("project", sa.Column("processed_upto_chapter", sa.Integer()))
    op.create_foreign_key(
        "fk_project_source_novel", "project", "novel",
        ["source_novel_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_project_source_novel", "project", type_="foreignkey")
    op.drop_column("project", "processed_upto_chapter")
    op.drop_column("project", "source_novel_id")
