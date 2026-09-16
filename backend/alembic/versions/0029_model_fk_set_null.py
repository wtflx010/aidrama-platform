"""Model FK ondelete=SET NULL + video_clip.model_id nullable

Problem: 6 张表（task/keyframe/video_clip/voice_line/asset/episode_video）引用
model.id 的外键无 ondelete 策略，且 video_clip.model_id 为 NOT NULL。
删除被引用的模型配置时触发 ForeignKeyViolation，导致"模型无法删除"。

Fix:
1. 全部 6 个外键加 ondelete=SET NULL（删除模型时引用自动置空，媒体行保留）
2. video_clip.model_id DROP NOT NULL（模型删除后视频片段仍保留，model_id 置空）

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 需要修复的 FK：(table, column, constraint_name)
FKS = [
    ("task", "model_id", "task_model_id_fkey"),
    ("keyframe", "model_id", "keyframe_model_id_fkey"),
    ("video_clip", "model_id", "video_clip_model_id_fkey"),
    ("voice_line", "model_id", "voice_line_model_id_fkey"),
    ("asset", "model_id", "asset_model_id_fkey"),
    ("episode_video", "model_id", "episode_video_model_id_fkey"),
]


def upgrade() -> None:
    # 1. 重建 6 个外键约束，加 ondelete=SET NULL
    for table, column, constraint_name in FKS:
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            "model",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
    # 2. video_clip.model_id 改为 nullable（模型删除后视频片段保留，model_id 置空）
    op.alter_column("video_clip", "model_id",
                    existing_type=sa.UUID(),
                    nullable=True)


def downgrade() -> None:
    # video_clip.model_id 恢复 NOT NULL（有 NULL 值时需先回填，此处不处理）
    op.alter_column("video_clip", "model_id",
                    existing_type=sa.UUID(),
                    nullable=False)
    # 恢复无 ondelete 的外键
    for table, column, constraint_name in FKS:
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            "model",
            [column],
            ["id"],
        )
