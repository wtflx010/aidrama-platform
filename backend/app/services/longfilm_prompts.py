"""连续长片模式提示词规则（2026-09）：适配「一集一条整片」的承接型文案。

不做「独立短片」的重起势，改成「连续长片的一环」：承接上一镜运动/机位/位置/光线。
另提供集级 global prompt 组装（统一基调），供 AIMixer 导演台 global.prompt 使用。
"""
from __future__ import annotations

_CONTINUITY_HEAD = (
    "承接上一镜结尾：保持人物姿态/站位/朝向/景别与上一镜一致，"
    "运动与运镜延续上一镜的方向与节奏，避免重新起势；"
    "灯光/色调与上一镜连续，作为连续拍摄中的一环。"
)

_SKIN_REALISM_BLOCK = (
    "人物面部为写实真人皮肤质感：保留自然毛孔、细纹、微瑕疵与肤色过渡；"
    "面部高光为柔和自然的漫射光，绝无油光溢脂、镜面刺眼反光或塑料蜡像质感；"
    "适度曝光，人物面部不过曝、不油亮、不磨皮。"
)


def compose_segment_prompt(seg, base_prompt: str | None = None, prev_seg=None) -> str:
    """单镜提示词（含连续长片承接头）。base 缺省用 enhanced_prompt/description。"""
    base = (base_prompt or "").strip() or (getattr(seg, "enhanced_prompt", None) or "").strip() \
        or (getattr(seg, "description", None) or "").strip()
    if prev_seg is None:
        return base
    prev_desc = (getattr(prev_seg, "description", None) or "").strip()
    head = _CONTINUITY_HEAD
    if prev_desc:
        head += f"上一镜结尾参考（仅作姿态/位置/情绪衔接）：{prev_desc[:120]}"
    return head + "\n" + base


def build_episode_global_prompt(episode, segments) -> str:
    """集级 global prompt：统一角色/场景/风格基调（供导演台 global.prompt）。"""
    parts: list[str] = []
    if getattr(episode, "title", None):
        parts.append(f"本集主题：{episode.title}")
    if getattr(episode, "synopsis", None):
        parts.append(episode.synopsis)
    scenes = sorted({s.scene_id for s in segments if getattr(s, "scene_id", None)})
    chars: set[str] = set()
    for s in segments:
        for c in (getattr(s, "character_ids", None) or []):
            chars.add(str(c))
    if scenes:
        parts.append("场景集：" + "、".join(scenes))
    if chars:
        parts.append("角色集：" + "、".join(sorted(chars)))
    body = "；".join(p for p in parts if p)
    if body:
        body += "。全片风格、角色外貌、场景空间保持统一，各镜头为同一连续拍摄的不同机位/时刻。"
        body += " " + _SKIN_REALISM_BLOCK
    else:
        body = _SKIN_REALISM_BLOCK
    return body
