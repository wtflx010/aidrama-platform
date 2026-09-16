"""task_type 枚举新增 generate_shot_plan（手工导入剧本「生成分镜」任务）。

Revision ID: 0080_generate_shot_plan
Revises: 0079_director_generate
"""
from typing import Union

from alembic import op

revision: str = "0080_generate_shot_plan"
down_revision: Union[str, None] = "0079_director_generate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 与 0064 canvas_generate 同法：PostgreSQL enum 增加新值（幂等）
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_shot_plan'")


def downgrade() -> None:
    # enum 移除成本高，不回退
    pass
