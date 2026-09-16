"""segment 表新增九宫格运镜规划字段（P5 导演层序列规划）。

新增 3 个可空字段（不破坏现有数据）：
- composition_point String(16)：九宫格构图点位
  center|left|right|top|bottom|top_left|top_right|bottom_left|bottom_right
- camera_params JSONB：运镜参数 {speed: slow|medium|fast, angle: front|side|low|high|top, intensity: subtle|normal|strong}
- lighting_continuity Text：光色接续约束（与上一镜色调/主光方向接续），空表示幕首镜自定基调

Revision ID: 0020
Revises: 0019
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segment", sa.Column("composition_point", sa.String(16)))
    op.add_column("segment", sa.Column("camera_params", postgresql.JSONB))
    op.add_column("segment", sa.Column("lighting_continuity", sa.Text()))


def downgrade() -> None:
    op.drop_column("segment", "lighting_continuity")
    op.drop_column("segment", "camera_params")
    op.drop_column("segment", "composition_point")
