"""删除 segment 表九宫格运镜规划字段（P5 已废弃）。

方案修订：放弃「九宫格运镜」体系，改为「幕级视频」方案（每幕一段长视频，
幕首/尾关键帧 + 分镜时间轴描述）。删除 P5 遗留的三个可空字段：
- composition_point String(16)
- camera_params JSONB
- lighting_continuity Text

Revision ID: 0024
Revises: 0023
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("segment", "lighting_continuity")
    op.drop_column("segment", "camera_params")
    op.drop_column("segment", "composition_point")


def downgrade() -> None:
    op.add_column("segment", sa.Column("composition_point", sa.String(16)))
    op.add_column("segment", sa.Column("camera_params", postgresql.JSONB))
    op.add_column("segment", sa.Column("lighting_continuity", sa.Text()))
