"""art_style 预设风格表 + project.style_id 改 UUID FK + project.art_style_prompt。

Revision ID: 0013
Revises: 0012
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels = None
depends_on = None


# 内置预设风格 Seed 数据
_BUILTIN_STYLES = [
    {
        "name": "电影感",
        "category": "写实",
        "prompt_fragment": "cinematic, film grain, dramatic lighting, shallow depth of field, 35mm",
        "description": "电影级画面，戏剧性光影，浅景深",
        "sort_order": 1,
    },
    {
        "name": "写实摄影",
        "category": "写实",
        "prompt_fragment": "photorealistic, high detail, natural lighting, 8k uhd",
        "description": "逼真写实，自然光线",
        "sort_order": 2,
    },
    {
        "name": "动漫",
        "category": "动漫",
        "prompt_fragment": "anime style, cel shading, vibrant colors, studio ghibli",
        "description": "日系动漫，赛璐璐上色",
        "sort_order": 3,
    },
    {
        "name": "水墨",
        "category": "国风",
        "prompt_fragment": "chinese ink painting, sumi-e, monochrome, traditional brush stroke",
        "description": "传统水墨，写意笔触",
        "sort_order": 4,
    },
    {
        "name": "赛博朋克",
        "category": "科幻",
        "prompt_fragment": "cyberpunk, neon lights, futuristic, blade runner aesthetic, high tech low life",
        "description": "霓虹未来，赛博朋克",
        "sort_order": 5,
    },
    {
        "name": "水彩",
        "category": "插画",
        "prompt_fragment": "watercolor painting, soft colors, hand painted, artistic",
        "description": "柔和水彩，手绘质感",
        "sort_order": 6,
    },
    {
        "name": "油画",
        "category": "插画",
        "prompt_fragment": "oil painting, thick brush strokes, classical art, renaissance",
        "description": "古典油画，厚重笔触",
        "sort_order": 7,
    },
    {
        "name": "3D渲染",
        "category": "3D",
        "prompt_fragment": "3d render, octane render, pixar style, smooth shading",
        "description": "3D 渲染，皮克斯风格",
        "sort_order": 8,
    },
    {
        "name": "像素艺术",
        "category": "复古",
        "prompt_fragment": "pixel art, 8-bit, retro game style, blocky",
        "description": "像素风，复古游戏",
        "sort_order": 9,
    },
    {
        "name": "黑白",
        "category": "单色",
        "prompt_fragment": "black and white, monochrome, high contrast, film noir",
        "description": "黑白摄影，高对比",
        "sort_order": 10,
    },
]


def upgrade() -> None:
    # 1) 创建 art_style 表
    op.create_table(
        "art_style",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("prompt_fragment", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("cover_url", sa.String(512)),
        sa.Column("reference_images", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 2) project 表：style_id 从 String(64) 改为 UUID FK
    #    先 drop 旧列，再重建为新类型（旧 style_id 是死字段，无数据损失）
    op.drop_column("project", "style_id")
    op.add_column(
        "project",
        sa.Column("style_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("art_style.id", ondelete="SET NULL")),
    )
    # 3) project 表：新增 art_style_prompt
    op.add_column("project", sa.Column("art_style_prompt", sa.Text()))

    # 4) Seed 内置预设风格
    style_table = sa.table(
        "art_style",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("category", sa.String),
        sa.column("prompt_fragment", sa.Text),
        sa.column("description", sa.Text),
        sa.column("reference_images", postgresql.JSONB),
        sa.column("sort_order", sa.Integer),
        sa.column("is_builtin", sa.Boolean),
    )
    import uuid as _uuid
    for s in _BUILTIN_STYLES:
        op.bulk_insert(
            style_table,
            [{
                "id": _uuid.uuid4(),
                "name": s["name"],
                "category": s["category"],
                "prompt_fragment": s["prompt_fragment"],
                "description": s["description"],
                "reference_images": [],
                "sort_order": s["sort_order"],
                "is_builtin": True,
            }],
        )


def downgrade() -> None:
    # 恢复 project.style_id 为 String(64)
    op.drop_column("project", "art_style_prompt")
    op.drop_column("project", "style_id")
    op.add_column("project", sa.Column("style_id", sa.String(64)))
    op.drop_table("art_style")
