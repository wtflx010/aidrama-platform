"""新增内置风格「仿真人效果」（真人实拍级写实，面向短剧生成）。

2026-08-10：用户需要在风格列表新增「仿真人效果」——AI 生成真人实拍质感的
短剧画面（真实人物、自然表情、真实光影），区别于「写实摄影」（静态肖像）
与「AI 漫剧」（半写实二次元）。幂等：name 已存在则跳过。

Revision ID: 0032
Revises: 0031
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels = None
depends_on = None

# 仿真人效果：真人实拍级写实（面向短剧），prompt_fragment 为多维度强描述
# （媒介/人物/表情/光影/质感/色彩），与 0017 强风格关键词规范一致。
# 2026-08-10 调研补强：保留自然毛孔/细纹/瑕疵、眼神有神、人景光影统一等"仿真人"关键点。
_BUILTIN_STYLE = {
    "name": "仿真人效果",
    "category": "写实",
    "prompt_fragment": (
        "photorealistic live-action film still of a realistic Chinese short drama, "
        "real human actor with authentic facial features, natural skin texture with "
        "visible pores, fine wrinkles and subtle imperfections, realistic body "
        "proportions, expressive eyes with natural micro-expressions, genuine candid "
        "facial expression, natural hair strands and fabric detail, cinematic dramatic "
        "lighting with soft key light and unified light direction, believable human "
        "performance, true-to-life color grading, 8k uhd, documentary-level realism"
    ),
    "description": (
        "仿真人效果（真人实拍级写实）：AI 生成真人实拍质感的短剧画面，"
        "角色真实自然、表情生动、光影真实，适合都市/情感/逆袭等真人短剧题材"
    ),
    "sort_order": 101,
}


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(
        sa.text("SELECT 1 FROM art_style WHERE name = :name"),
        {"name": _BUILTIN_STYLE["name"]},
    ).first()
    if exists:
        return
    conn.execute(
        sa.text(
            "INSERT INTO art_style "
            "(id, name, category, prompt_fragment, description, cover_url, "
            " reference_images, sort_order, is_builtin, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :name, :category, :fragment, :description, NULL, "
            " '[]'::jsonb, :sort_order, true, now(), now())"
        ),
        {
            "name": _BUILTIN_STYLE["name"],
            "category": _BUILTIN_STYLE["category"],
            "fragment": _BUILTIN_STYLE["prompt_fragment"],
            "description": _BUILTIN_STYLE["description"],
            "sort_order": _BUILTIN_STYLE["sort_order"],
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM art_style WHERE name = :name AND is_builtin = true"),
        {"name": _BUILTIN_STYLE["name"]},
    )
