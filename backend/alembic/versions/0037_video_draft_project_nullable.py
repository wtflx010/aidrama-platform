"""AI 视频独立化：video_draft.project_id 改为可空（独立功能，不依赖项目）。

Revision ID: 0037
Revises: 0036
"""
from typing import Union
from alembic import op

revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("video_draft", "project_id", nullable=True)


def downgrade() -> None:
    # 仅置空行（无项目的全局草稿）无法还原 NOT NULL，回滚仅记录说明
    op.alter_column("video_draft", "project_id", nullable=False)
