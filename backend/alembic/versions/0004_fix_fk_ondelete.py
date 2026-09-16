"""Fix FK constraints: ondelete=SET NULL for nullable task_id/keyframe_id columns

Problem: video_clip.task_id, keyframe.task_id, voice_line.task_id, asset.task_id,
video_clip.keyframe_id all reference task.id / keyframe.id WITHOUT ondelete.
When deleting a Project, SQLAlchemy ORM batch-deletes Tasks before the media rows
that reference them, triggering ForeignKeyViolation.

Fix: add ondelete=SET NULL to all these nullable FKs. When a task/keyframe is
deleted, PostgreSQL sets the referencing column to NULL, allowing the cascade
to proceed. The media rows themselves are later deleted via segment_id CASCADE.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 需要修复的 FK 约束：(table, column, referenced_table, constraint_name)
# 全部是 nullable 列，用 SET NULL：删除 task/keyframe 时把引用置空，媒体行后续由 segment cascade 清理
FKS = [
    ("keyframe", "task_id", "task", "keyframe_task_id_fkey"),
    ("video_clip", "task_id", "task", "video_clip_task_id_fkey"),
    ("video_clip", "keyframe_id", "keyframe", "video_clip_keyframe_id_fkey"),
    ("voice_line", "task_id", "task", "voice_line_task_id_fkey"),
    ("asset", "task_id", "task", "asset_task_id_fkey"),
]


def upgrade() -> None:
    for table, column, ref_table, constraint_name in FKS:
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            ref_table,
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table, column, ref_table, constraint_name in FKS:
        op.drop_constraint(constraint_name, table, type_="foreignkey")
        op.create_foreign_key(
            constraint_name,
            table,
            ref_table,
            [column],
            ["id"],
        )
