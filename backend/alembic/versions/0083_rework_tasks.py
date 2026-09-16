"""task_type 新增 reframe_video / voice_change_video / draw_to_video（P1-4 修片工作流）。

Revision ID: 0083
Revises: 0082
"""
from typing import Union
from alembic import op

revision: str = "0083_rework_tasks"
down_revision: Union[str, None] = "0082_voice_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in ("reframe_video", "voice_change_video", "draw_to_video"):
            op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS '" + value + "'")


def downgrade() -> None:
    pass
