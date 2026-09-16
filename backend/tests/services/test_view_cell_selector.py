"""机位格子选择器测试（2026-08-30）：分镜机位关键词 → 多视图网格格子。"""
from unittest.mock import patch

from app.services.view_cell_selector import (
    pick_character_cell,
    pick_scene_cell,
    score_view,
)

# 室内 scene_shots（与真实 POV 组同构）
_SHOT_NAMES = [
    "top_anchor", "doorway_eye", "window_lookback", "center_sweep", "corner_low", "far_corner_high",
]
_SHOT_TEXTS = [
    "Panel 1 (top-left): TOP ANCHOR view - camera directly above the room, looking straight down",
    "Panel 2 (top-center): EYE-LEVEL doorway view - camera at the room doorway, looking into the room",
    "Panel 3 (top-right): WINDOW REAR view - camera at the window side, looking BACK toward the doorway",
    "Panel 4 (bottom-left): CENTER SWEEP view - camera in the middle of the room, a wide panorama",
    "Panel 5 (bottom-center): CORNER LOW view - camera low in a corner",
    "Panel 6 (bottom-right): FAR CORNER HIGH view - camera far corner high, down across the whole room",
]
# 室外 scene_shots（含 left_low_shot / right_eye / far_wide）
_EXT_NAMES = [
    "top_anchor", "entrance_eye", "deep_lookback", "left_low_shot", "right_eye", "far_wide",
]
_EXT_TEXTS = [
    "Panel 1 (top-left): TOP ANCHOR view - camera directly above the street, looking straight down",
    "Panel 2 (top-center): EYE-LEVEL entrance view - camera at the street entrance, eye-level",
    "Panel 3 (top-right): DEEP REAR view - camera at the far end turned around, looking BACK",
    "Panel 4 (bottom-left): LOW LEFT view - camera on the left sidewalk, low eye-level",
    "Panel 5 (bottom-center): RIGHT view - camera on the right sidewalk, eye-level",
    "Panel 6 (bottom-right): FAR WIDE establishing view - camera far back, whole street, skyline",
]


def test_score_view_basics():
    assert score_view("从高处俯视整个房间") == "top"
    assert score_view("正对大门，看向房间深处") == "front"
    assert score_view("回头看向背后") == "back"
    assert score_view("从左侧看过去") == "left"
    assert score_view("从右侧看过去") == "right"
    assert score_view("整个房间的远景") == "wide"
    assert score_view("侧面镜头") == "side"


def test_pick_scene_cell_top():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/view0.png') as m:
        got = pick_scene_cell('http://x/sheet.png', _SHOT_NAMES, _SHOT_TEXTS, ['俯视整个空间'])
    assert got is not None
    assert got[1] == "top" and got[2] == 0


def test_pick_scene_cell_front():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/view1.png') as m:
        got = pick_scene_cell('http://x/sheet.png', _SHOT_NAMES, _SHOT_TEXTS, ['正对大门，看向房间'])
    assert got is not None
    assert got[1] == "front" and got[2] == 1


def test_pick_scene_cell_back():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/view2.png') as m:
        got = pick_scene_cell('http://x/sheet.png', _SHOT_NAMES, _SHOT_TEXTS, ['回头看向背后的门口'])
    assert got is not None
    assert got[1] == "back" and got[2] == 2


def test_pick_scene_cell_left_outdoor():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/view3.png') as m:
        got = pick_scene_cell('http://x/sheet.png', _EXT_NAMES, _EXT_TEXTS, ['从左侧看街道'])
    assert got is not None
    assert got[1] == "left" and got[2] == 3


def test_pick_scene_cell_wide():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/view5.png') as m:
        got = pick_scene_cell('http://x/sheet.png', _SHOT_NAMES, _SHOT_TEXTS, ['整个房间的远景'])
    assert got is not None
    assert got[1] == "wide" and got[2] == 5


def test_pick_scene_cell_no_keyword_returns_none():
    got = pick_scene_cell('http://x/sheet.png', _SHOT_NAMES, _SHOT_TEXTS, ['两个人说话'])
    assert got is None


def test_pick_character_cell_front():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/char1.png') as m:
        got = pick_character_cell('http://x/char.png', ['正对镜头对话'])
    assert got is not None
    assert got[1] == "front" and got[2] == 1


def test_pick_character_cell_back():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/char3.png') as m:
        got = pick_character_cell('http://x/char.png', ['背影离开'])
    assert got is not None
    assert got[1] == "back" and got[2] == 3


def test_pick_character_cell_side():
    with patch('app.utils.media.crop_grid_cell', return_value='http://x/char2.png') as m:
        got = pick_character_cell('http://x/char.png', ['侧面'])
    assert got is not None
    assert got[1] == "side" and got[2] == 2

