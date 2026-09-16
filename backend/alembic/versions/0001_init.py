"""init schema

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 枚举类型（由 create_table 自动创建，此处仅定义）
    project_status = sa.Enum("draft", "generating", "done", "failed", name="project_status")
    media_status = sa.Enum("pending", "running", "succeeded", "failed", name="media_status")
    task_type = sa.Enum("generate_keyframe", "generate_video", "export_film", name="task_type")
    task_status = sa.Enum("pending", "running", "succeeded", "failed", "cancelled", name="task_status")
    provider_type = sa.Enum("openai_compatible", "http_poll", "openai_tts", name="provider_type")
    model_type = sa.Enum("text", "image", "video", "tts", name="model_type")

    op.create_table(
        "project",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="local"),
        sa.Column("synopsis", sa.Text()),
        sa.Column("script", sa.Text()),
        sa.Column("style_id", sa.String(64)),
        sa.Column("aspect_ratio", sa.String(16), nullable=False, server_default="16:9"),
        sa.Column("resolution", sa.String(16), nullable=False, server_default="HD"),
        sa.Column("status", project_status, nullable=False, server_default="draft"),
        sa.Column("cover_url", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "episode",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default="主幕"),
        sa.Column("synopsis", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "index", name="uq_episode_project_index"),
    )

    op.create_table(
        "segment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("episode.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("shot_type", sa.String(32)),
        sa.Column("camera", sa.String(32)),
        sa.Column("description", sa.Text()),
        sa.Column("dialogue", sa.Text()),
        sa.Column("narration", sa.Text()),
        sa.Column("duration", sa.Float, nullable=False, server_default="5.0"),
        sa.Column("character_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("scene_id", sa.String(64)),
        sa.Column("prop_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("episode_id", "index", name="uq_segment_episode_index"),
    )

    op.create_table(
        "model",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider_type", provider_type, nullable=False),
        sa.Column("model_type", model_type, nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(512), nullable=False),
        sa.Column("api_key_ref", sa.String(100), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("capability", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("credits_per_unit", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scene_codes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("http_poll_config", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_model_type_enabled", "model", ["model_type", "is_enabled"])

    op.create_table(
        "task",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", task_type, nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model.id")),
        sa.Column("status", task_status, nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("result_url", sa.String(512)),
        sa.Column("provider", sa.String(64)),
        sa.Column("provider_task_id", sa.String(128)),
        sa.Column("poll_url", sa.String(512)),
        sa.Column("cost_credits", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_task_project_status", "task", ["project_id", "status"])
    op.create_index("ix_task_target", "task", ["target_type", "target_id"])
    op.create_index("ix_task_status_created", "task", ["status", "created_at"])

    op.create_table(
        "keyframe",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("segment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("image_url", sa.String(512)),
        sa.Column("status", media_status, nullable=False, server_default="pending"),
        sa.Column("used_as_video_first_frame", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model.id")),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task.id")),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "video_clip",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("segment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyframe_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("keyframe.id")),
        sa.Column("prompt", sa.Text()),
        sa.Column("num_frames", sa.Integer, nullable=False, server_default="121"),
        sa.Column("frame_rate", sa.Integer, nullable=False, server_default="24"),
        sa.Column("width", sa.Integer, nullable=False, server_default="1280"),
        sa.Column("height", sa.Integer, nullable=False, server_default="720"),
        sa.Column("first_frame_url", sa.String(512)),
        sa.Column("last_frame_url", sa.String(512)),
        sa.Column("video_url", sa.String(512)),
        sa.Column("duration", sa.Float),
        sa.Column("status", media_status, nullable=False, server_default="pending"),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model.id"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task.id")),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("video_clip")
    op.drop_table("keyframe")
    op.drop_index("ix_task_status_created", table_name="task")
    op.drop_index("ix_task_target", table_name="task")
    op.drop_index("ix_task_project_status", table_name="task")
    op.drop_table("task")
    op.drop_index("ix_model_type_enabled", table_name="model")
    op.drop_table("model")
    op.drop_table("segment")
    op.drop_table("episode")
    op.drop_table("project")
    for name in ("model_type", "provider_type", "task_status", "task_type", "media_status", "project_status"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
