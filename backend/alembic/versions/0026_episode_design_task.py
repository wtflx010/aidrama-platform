"""P7.7 幕首/幕尾图任务：task_type 枚举新增 generate_episode_design。

拆分链路：先生成幕级设计图（幕首图/幕尾图，阶段1），确认后再生成幕级视频（阶段2）。

Revision ID: 0026
Revises: 0025
"""
from typing import Union
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_episode_design'")


def downgrade() -> None:
    # PG 枚举值不支持删除（有数据引用）；空实现，回滚时由 0025 全量重建
    pass
