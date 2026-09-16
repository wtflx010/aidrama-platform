"""director_generate 任务类型(画布导演台模式,2026-08-29)。

Revision ID: 0079_director_generate
Revises: 0078_segment_shot_beats
"""
from typing import Union

from alembic import op

revision: str = "0079_director_generate"
down_revision: Union[str, None] = "0078_segment_shot_beats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与 0064 canvas_generate 同法：PostgreSQL enum 增加新值（幂等）
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'director_generate'")


def downgrade() -> None:
    # enum 移除成本高，不回退
    pass
