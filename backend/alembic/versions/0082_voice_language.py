"""voice_line 加 language 列 + task_type 新增 dub_episode（P2-5 多语言配音字幕导出）。

Revision ID: 0082
Revises: 0081
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0082_voice_language"
down_revision: Union[str, None] = "0081_episode_eval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("voice_line", sa.Column("language", sa.String(16), nullable=False, server_default="zh-CN"))
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'dub_episode'")


def downgrade() -> None:
    op.drop_column("voice_line", "language")
