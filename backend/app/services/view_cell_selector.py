# -*- coding: utf-8 -*-
"""机位 → 多视图网格格子 选择器（2026-08-30）。

图生视频 R2V 参考改造：分镜带机位语义时，从场景多视图（3x2=6格）或角色
四视图（2x2=4格）网格图中裁剪出与分镜机位最匹配的一格，作为该镜参考图，
而不是把整张网格图丢给模型（网格线入画 / 模型不知道该看哪格）。

格子语义 = 格子 name/position 强规则 + view_text 关键词兜底：
- scene_shots/POV 组数组顺序 = Panel 1..6 = index 0..5
- 匹配规则：先按格子 name 的语义强规则（top_anchor/doorway/front/lookback/...），
  再按 view_text 关键词兜底，可规避 Panel 布局位置词干扰。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 机位关键词 → 语义名（命中加分；权重高者优先）
_VIEW_KEYWORDS: dict[str, dict[str, int]] = {
    "top": {
        "俯视": 5, "俯拍": 5, "鸟瞰": 5, "俯瞰": 5, "正上方": 5,
        "top-down": 5, "overhead": 5, "aerial": 4, "from above": 5,
        "looking straight down": 6, "looking down at": 5, "bird s eye": 5, "top anchor": 6,
        "斜上方": 5, "45度俯瞰": 6, "45 度俯瞰": 6, "oblique": 5, "risen": 5,
        "high vantage": 5, "elevated": 4, "down at": 3, "looking diagonally down": 6,
    },
    "front": {
        "正面": 4, "正对": 4, "正视": 4, "主视角": 3,
        "front": 4, "frontal": 4, "head-on": 4, "facing": 3,
        "looking into the room": 4, "toward the camera": 1, "straight ahead": 1, "entrance": 2,
    },
    "back": {
        "背": 4, "背对": 5, "背后": 5, "回望": 4, "背面": 4, "身后": 4,
        "back view": 5, "rear": 4, "behind": 4, "lookback": 5, "from behind": 5,
        "turned around": 5, "rear facade": 5, "opposite end": 3,
    },
    "side": {
        "侧面": 5, "侧视": 5, "侧方": 4, "侧向": 4, "3/4": 4, "四分之三": 4,
        "side": 5, "profile": 5, "from the side": 5, "3-4 view": 4,
    },
    "left": {
        "左侧": 4, "左边": 3, "左方": 3, "向左": 3, "low left": 5,
        "left": 4, "on the left": 4, "left side": 4, "from the left": 4, "left sidewalk": 5,
    },
    "right": {
        "右侧": 4, "右边": 3, "右方": 3, "向右": 3,
        "right": 4, "on the right": 4, "right side": 4, "from the right": 4, "right sidewalk": 5,
    },
    "wide": {
        "全景": 3, "远景": 5, "大全景": 4, "wide": 3, "panorama": 3,
        "wide shot": 5, "establishing": 5, "whole scene": 4, "horizon": 2,
        "skyline": 2, "far view": 5, "whole block": 4, "wide panorama": 5,
        "广角": 5, "远端": 4, "完整展示": 4, "全部空间": 4, "entire interior": 5,
        "entire space": 4, "whole room": 4, "whole interior": 4, "diagonal": 4,
        "corner": 4, "角落": 4, "对角线": 4, "纵深": 3, "对角": 4,
        "angled viewpoint": 2, "entire interior space": 6,
        "低机位": 4, "低角度": 4, "仰角": 4, "仰望": 4, "向上看": 4,
        "low camera": 4, "near the floor": 4, "low rise": 6, "upward tilt": 5,
        "building tops": 4, "looking up": 4, "near the ground": 4, "low vantage": 5,
    },
}

_LAYOUT_WORDS = [
    "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right",
    "panel 1", "panel 2", "panel 3", "panel 4", "panel 5", "panel 6",
]


def score_view(text: str, strip_layout: bool = False) -> str | None:
    """对一段文本做机位语义打分，返回语义名（top/front/back/left/right/wide）或 None。

    strip_layout=True 用于给"格子 view_text"打分：先剥离网格布局位置词
    （Panel N / top-left 等），因为这些是画布坐标不是机位朝向，会干扰
    left/right/top 的语义匹配（如 bottom-right 格会被误判为 right）。
    """
    t = text.lower()
    if strip_layout:
        for w in _LAYOUT_WORDS:
            t = t.replace(w, " ")
    best, best_score = None, 0.0
    for view, kws in _VIEW_KEYWORDS.items():
        s = 0.0
        for k, w in kws.items():
            if k.lower() in t:
                s += w
        if s > best_score:
            best, best_score = view, s
    return best if best_score > 0 else None


def pick_scene_cell(
    sheet_url: str,
    cell_names: list[str],
    cell_view_texts: list[str],
    hit_texts: list[str],
    prefix: str = "scene_cell",
) -> tuple[str, str, int] | None:
    """场景多视图 3x2：按分镜 hit_texts 机位语义选格子并裁剪，返回最匹配格。

    cell_names / cell_view_texts 长度必须为 6（Panel 1..6），索引一一对应。
    命中规则：先按格子 name 强规则（top_anchor/doorway_eye/front/lookback/...），
    再按 view_text 关键词兜底。返回 (cell_url, view_name, index) 或 None。"""
    from app.utils.media import crop_grid_cell

    nc = cell_names if isinstance(cell_names, list) else []
    nv = cell_view_texts if isinstance(cell_view_texts, list) else []
    if len(nc) != 6 or len(nv) != 6:
        logger.warning("[viewcell] scene_sheet 格子信息异常 names=%s texts=%s", len(nc), len(nv))
        return None

    # name → 语义强规则
    _NAME_RULES: list[tuple[str, str]] = [
        ("top_anchor", "top"), ("top", "top"), ("oblique_top", "top"),
        ("doorway", "front"), ("entrance", "front"), ("front", "front"),
        ("lookback", "back"), ("rear", "back"), ("rear_offset", "back"), ("back", "back"),
        ("left", "left"),
        ("right", "right"),
        ("far_", "wide"), ("far-", "wide"), ("wide", "wide"),
        ("sweep", "wide"), ("panorama", "wide"), ("low_rise", "wide"),
        ("corner_diagonal", "wide"), ("corner", "wide"), ("diagonal", "wide"),
    ]

    def cell_semantic(i: int) -> str | None:
        name = (nc[i] or "").lower()
        for k, view in _NAME_RULES:
            if k in name:
                return view
        return score_view(nv[i] or "", strip_layout=True)

    hit_view = score_view(" ".join(hit_texts))
    if hit_view is None:
        logger.info("[viewcell] 分镜无机位关键词，整图退化（不裁剪）")
        return None

    candidates: list[tuple[float, int]] = []
    for i in range(6):
        sv = cell_semantic(i)
        if sv != hit_view:
            continue
        # 格 view_text 内该语义关键词命中加分
        gvt = (nv[i] or "").lower()
        s = 10.0
        for k, w in _VIEW_KEYWORDS.get(hit_view, {}).items():
            if k.lower() in gvt:
                s += w * 0.4
        # wide 语义下，名字含 far/establishing 的格子偏好（远全景优先）
        if hit_view == "wide" and ("far" in (nc[i] or "").lower() or "establish" in gvt):
            s += 5.0
        candidates.append((s, i))
    if not candidates:
        logger.info("[viewcell] 场景机位 %s 无对应格子，退回封面", hit_view)
        return None
    best_s, best_idx = max(candidates)
    if best_s < 10.5:
        logger.info("[viewcell] 场景机位 %s 弱匹配格子 %s (score=%.1f)，退回封面", hit_view, best_idx, best_s)
        return None

    url = crop_grid_cell(sheet_url, (3, 2), best_idx, out_prefix=prefix)
    if url is None:
        return None
    logger.info("[viewcell] 场景机位 %s -> 格子 %s (%s)", hit_view, best_idx, url)
    return url, hit_view, best_idx


# 角色四视图 2x2 格子语义（CharacterSheet LoRA 惯例）：index 0=半身/1=正面/2=侧面/3=背面
_CHAR_CELL_VIEWS = [
    "half body portrait of the character, waist up, uncropped, head and torso visible",
    "front view full body of the character, in a relaxed front-facing pose with the gaze directed slightly off-camera",
    "side view full body of the character, profile, 3-4 view",
    "back view full body of the character, rear view, from behind",
]


def pick_character_cell(
    sheet_url: str,
    hit_texts: list[str],
    prefix: str = "char_cell",
) -> tuple[str, str, int] | None:
    """角色四视图 2x2：按分镜机位选格子裁剪。返回 (cell_url, view_name, index) 或 None。"""
    from app.utils.media import crop_grid_cell

    hit = " ".join(hit_texts)
    hit_view = score_view(hit)
    if hit_view is None:
        return None
    mapping = {"top": 0, "front": 1, "side": 2, "back": 3}
    if hit_view in ("left", "right"):
        hit_view = "side"  # 左右侧视统一算"侧面格"
    idx = mapping.get(hit_view)
    if idx is None:
        return None
    url = crop_grid_cell(sheet_url, (2, 2), idx, out_prefix=prefix)
    if url is None:
        return None
    logger.info("[viewcell] 角色机位 %s -> 格子 %s (%s)", hit_view, idx, url)
    return url, hit_view, idx
