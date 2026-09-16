"""P7 幕级视频：episode 字段 + episode_video 表 + task_type 枚举。

1. episode 表新增：
   - video_script Text | None：LLM 生成的幕级时间轴分镜描述
   - video_status String(16) default 'none'：幕级视频整体状态
2. 新建 episode_video 表（每幕分段记录，取代逐镜视频）
3. task_type 枚举新增 generate_episode_video

Revision ID: 0025
Revises: 0024
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episode", sa.Column("video_script", sa.Text(), nullable=True))
    op.add_column(
        "episode",
        sa.Column("video_status", sa.String(16), nullable=False, server_default="none"),
    )
    op.create_table(
        "episode_video",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("episode_id", sa.Uuid(), sa.ForeignKey("episode.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("num_frames", sa.Integer(), nullable=False, server_default="441"),
        sa.Column("frame_rate", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="1280"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="720"),
        sa.Column("first_frame_url", sa.String(512), nullable=True),
        sa.Column("last_frame_url", sa.String(512), nullable=True),
        sa.Column("video_url", sa.String(512), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("status", postgresql.ENUM("pending", "running", "succeeded", "failed",
                                            name="media_status", create_type=False),
                  nullable=False, server_default="pending"),
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("model.id"), nullable=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'generate_episode_video'")


def downgrade() -> None:
    op.drop_table("episode_video")
    op.drop_column("episode", "video_status")
    op.drop_column("episode", "video_script")
