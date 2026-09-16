"""更新内置美术风格的 prompt_fragment 为强风格关键词（P4.1 风格差异化）。

旧 fragment 只有 4~6 个弱词（如 "cinematic, film grain"），FLUX 等模型对纯风格词
响应不足，导致 10 种风格生成趋同。改为 40~80 词的多维度强描述
（媒介/技法/色彩/质感/参考风格），确保模型能显著区分各风格。

Revision ID: 0017
Revises: 0016
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels = None
depends_on = None

# name → 强风格关键词（保留原 id，仅更新 prompt_fragment）
_STRONG_STYLE_PROMPTS = {
    "电影感": (
        "cinematic film still, blockbuster movie cinematography, anamorphic widescreen "
        "composition, dramatic chiaroscuro lighting, teal and orange color grade, shallow "
        "depth of field, 35mm film grain, atmospheric haze, lens flare, high contrast "
        "shadows, epic hollywood staging"
    ),
    "写实摄影": (
        "hyper-realistic professional photography, shot on 85mm f/1.4 lens, natural soft "
        "window lighting, true-to-life skin texture and pores, crisp tack-sharp focus, "
        "accurate real-world colors, editorial portrait photography, national geographic "
        "style, 8k uhd detail, candid realistic moment"
    ),
    "动漫": (
        "japanese anime style, hand-drawn cel shading, clean bold lineart, vibrant "
        "saturated colors, studio ghibli and makoto shinkai inspired, soft painterly "
        "background, expressive large eyes, 2d animation film keyframe, smooth gradients, "
        "dreamy atmosphere"
    ),
    "水墨": (
        "traditional chinese ink wash painting, shuimo shanshui landscape, sumi-e brush "
        "strokes, black ink on xuan paper, monochrome with ink wash gradients, flowing "
        "calligraphic brushwork, generous negative space, xieyi freehand style, subtle "
        "paper texture, poetic minimalism"
    ),
    "赛博朋克": (
        "cyberpunk dystopia, neon-soaked megacity at night, magenta and cyan neon glow, "
        "rain-slicked reflective streets, blade runner 2049 aesthetic, holographic "
        "billboards, futuristic high-tech low-life atmosphere, volumetric fog, electric "
        "city lights, gritty cybernetic detail"
    ),
    "水彩": (
        "delicate watercolor painting, transparent layered color washes, soft pastel "
        "palette, visible paper grain and texture, wet-on-wet blending, gentle color "
        "bleeding edges, hand-painted art print, light airy luminous feel, loose "
        "expressive strokes"
    ),
    "油画": (
        "classical oil painting, thick impasto brush strokes, renaissance baroque style, "
        "rich warm earthy palette, visible canvas texture, dramatic rembrandt lighting, "
        "chiaroscuro depth, old masters museum masterpiece, ornate gilded frame aesthetic"
    ),
    "3D渲染": (
        "high-end 3d render, pixar style 3d animation film still, octane render, smooth "
        "subsurface scattering, soft studio lighting, glossy reflections, clean stylized "
        "geometry, disney dreamworks quality, vibrant cg colors, polished character "
        "animation look"
    ),
    "像素艺术": (
        "16-bit pixel art, retro video game sprite, chunky visible pixels, limited color "
        "palette, dithering gradients, NES SNES aesthetic, arcade game screen, nostalgic "
        "90s pixel graphics, crisp blocky shapes, retro gaming charm"
    ),
    "黑白": (
        "black and white photography, monochrome film noir, extreme high contrast "
        "lighting, deep blacks and pure whites, dramatic hard shadows, vintage 1940s noir "
        "style, silver gelatin print texture, grainy analog film, chiaroscuro moody "
        "atmosphere"
    ),
}


def upgrade() -> None:
    conn = op.get_bind()
    for name, frag in _STRONG_STYLE_PROMPTS.items():
        conn.execute(
            sa.text("UPDATE art_style SET prompt_fragment = :frag WHERE name = :name"),
            {"frag": frag, "name": name},
        )


def downgrade() -> None:
    # 旧值已在 0013 seed 中定义，回滚不还原（数据不可逆），留空由人工处理。
    pass
