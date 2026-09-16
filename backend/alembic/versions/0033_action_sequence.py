"""P 白模故事版：action_sequence 表 + segment.action_sequence 列。

1. 新建 action_sequence 表（挂幕，存白模模板图 + 格子分组 + 逐组视频 + 拼接产物）
2. segment 表新增 action_sequence String(64)：动作序列标记（打斗/动作段连续分镜
   打同一标记，如 as_1），据此聚合分镜做动作序列展示
3. task_type 枚举新增 generate_action_sequence_template

Revision ID: 0033
Revises: 0032
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_sequence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("episode_id", sa.Uuid(), sa.ForeignKey("episode.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_key", sa.String(64), nullable=False),
        sa.Column("segment_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("template_url", sa.String(512), nullable=True),
        sa.Column("grid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("groups", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("videos_url", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("composed_url", sa.String(512), nullable=True),
        sa.Column("composed_duration", sa.Float(), nullable=True),
        sa.Column("status", postgresql.ENUM("pending", "running", "succeeded", "failed",
                                            name="media_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("segment", sa.Column("action_sequence", sa.String(64), nullable=True))
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_action_sequence_template'")


def downgrade() -> None:
    op.drop_column("segment", "action_sequence")
    op.drop_table("action_sequence")
