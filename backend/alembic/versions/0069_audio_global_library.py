"""音乐/音效全局音频库改造：
1) bgm_track.project_id 改 nullable（ondelete CASCADE → SET NULL），episode_id 改 SET NULL
2) sfx_clip 新增 project_id（SET NULL）、segment_id 改 nullable（SET NULL）
3) 新建 project_bgm 绑定表；存量 BGM 归属项目写入绑定表
4) 存量 SFX 通过 segment→episode 联表回填 project_id

Revision ID: 0069
Revises: 0068
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) BGM
    op.drop_constraint("bgm_track_project_id_fkey", "bgm_track", type_="foreignkey")
    op.create_foreign_key(
        "bgm_track_project_id_fkey", "bgm_track", "project",
        ["project_id"], ["id"], ondelete="SET NULL",
    )
    op.alter_column("bgm_track", "project_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    op.drop_constraint("bgm_track_episode_id_fkey", "bgm_track", type_="foreignkey")
    op.create_foreign_key(
        "bgm_track_episode_id_fkey", "bgm_track", "episode",
        ["episode_id"], ["id"], ondelete="SET NULL",
    )
    op.alter_column("bgm_track", "episode_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    # 2) SFX：新增 project_id + segment_id 可空
    op.add_column("sfx_clip", sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="SET NULL"), nullable=True))
    op.drop_constraint("sfx_clip_segment_id_fkey", "sfx_clip", type_="foreignkey")
    op.create_foreign_key(
        "sfx_clip_segment_id_fkey", "sfx_clip", "segment",
        ["segment_id"], ["id"], ondelete="SET NULL",
    )
    op.alter_column("sfx_clip", "segment_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=True)
    # 3) project_bgm 绑定表 + 存量迁移
    op.create_table(
        "project_bgm",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bgm_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("bgm_track.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bgm_id", "project_id", name="uq_project_bgm"),
    )
    op.execute(
        "INSERT INTO project_bgm (id, bgm_id, project_id, created_at, updated_at) "
        "SELECT gen_random_uuid(), id, project_id, now(), now() FROM bgm_track WHERE project_id IS NOT NULL"
    )
    # 4) SFX 回填 project_id（经 segment → episode）
    op.execute(
        "UPDATE sfx_clip SET project_id = e.project_id "
        "FROM segment s JOIN episode e ON e.id = s.episode_id "
        "WHERE sfx_clip.segment_id = s.id AND sfx_clip.project_id IS NULL"
    )


def downgrade() -> None:
    op.drop_table("project_bgm")
    op.drop_column("sfx_clip", "project_id")
    op.drop_constraint("sfx_clip_segment_id_fkey", "sfx_clip", type_="foreignkey")
    op.create_foreign_key(
        "sfx_clip_segment_id_fkey", "sfx_clip", "segment",
        ["segment_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("sfx_clip", "segment_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_constraint("bgm_track_episode_id_fkey", "bgm_track", type_="foreignkey")
    op.create_foreign_key(
        "bgm_track_episode_id_fkey", "bgm_track", "episode",
        ["episode_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("bgm_track", "episode_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_constraint("bgm_track_project_id_fkey", "bgm_track", type_="foreignkey")
    op.create_foreign_key(
        "bgm_track_project_id_fkey", "bgm_track", "project",
        ["project_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("bgm_track", "project_id", existing_type=sa.dialects.postgresql.UUID(as_uuid=True), nullable=False)
