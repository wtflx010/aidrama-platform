"""场景多视角 prompt 测试（2026-08-30 更新）：单俯视参考 + 用户定义六机位组。"""
from app.tasks.generate_scene_multiview import (
    _POV_SHOTS_INDOOR,
    _POV_SHOTS_OUTDOOR,
    _classify_space,
    _render_pov_grid_prompt,
)

_COVER = "http://localhost:8000/static/media/assets/x/cover.png"
_TOP = "http://localhost:8000/static/media/assets/x/scene_sheet_top.png"


def _single_top():
    return _render_pov_grid_prompt(
        _POV_SHOTS_INDOOR, "room", "a conference room", top_ref_url=_TOP,
    )


def _noref():
    return _render_pov_grid_prompt(
        _POV_SHOTS_INDOOR, "room", "a conference room",
    )


def test_single_top_mode():
    p = _single_top()
    assert "TOP-DOWN layout map of ONE scene" in p
    assert "Reconstruct THE EXACT SAME scene from that layout" in p
    assert "Create a SINGLE multi-panel scene reference sheet" not in p


def test_noref_txt2img_mode():
    p = _noref()
    assert "Create a SINGLE multi-panel scene reference sheet" in p
    assert "Grid layout (left to right)" in p


def test_indoor_six_camera_group():
    names = [s["name"] for s in _POV_SHOTS_INDOOR]
    assert names == [
        "far_wide", "side_shift", "oblique_top",
        "low_rise", "corner_diagonal", "rear_offset",
    ]
    p = _single_top()
    assert "FAR WIDE view" in p
    assert "SIDE SHIFT view" in p
    assert "OBLIQUE TOP view" in p
    assert "LOW RISE view" in p
    assert "DIAGONAL CORNER view" in p
    assert "REAR OFFSET view" in p


def test_outdoor_six_camera_group():
    names = [s["name"] for s in _POV_SHOTS_OUTDOOR]
    assert names == [
        "far_wide", "side_shift", "oblique_top",
        "low_rise", "corner_diagonal", "rear_offset",
    ]
    p = _render_pov_grid_prompt(_POV_SHOTS_OUTDOOR, "place", "a street", top_ref_url=_TOP)
    assert "FAR WIDE view" in p and "REAR OFFSET view" in p


def test_cover_single_reference_mode():
    # 2026-08-30: cover_ref_url = 封面作唯一参考（人类平视视角），非双参考。
    import inspect
    sig = inspect.signature(_render_pov_grid_prompt)
    assert "cover_ref_url" in sig.parameters
    p = _render_pov_grid_prompt(_POV_SHOTS_INDOOR, "room", "a hall",
                                cover_ref_url="http://x/cover.png")
    assert "human eye" in p
    assert "TOP-DOWN layout" not in p


def test_cover_scene_absolutely_locked():
    # 封面场景绝对不改、只换机位——硬约束必须出现在 cover 模式 prompt。
    p = _render_pov_grid_prompt(_POV_SHOTS_INDOOR, "room", "a hall", cover_ref_url="http://x/cover.png")
    assert "COVER SCENE LOCK (ABSOLUTE PRIORITY)" in p
    assert "COVER SCENE ABSOLUTE LOCK" in p
    assert "Do NOT redesign" in p
    assert "only difference permitted between" in p
    p2 = _render_pov_grid_prompt(_POV_SHOTS_INDOOR, "room", "a hall")
    assert "COVER SCENE ABSOLUTE LOCK" not in p2


def test_cover_mode_cameras_must_differ():
    # 2026-08-30+ 修复：封面锁只锁「场景内容」，机位必须显著拉开（六格不能趋同）。
    p = _render_pov_grid_prompt(_POV_SHOTS_INDOOR, "room", "a hall", cover_ref_url="http://x/cover.png")
    assert "CRITICAL CAMERA DIFFERENCE" in p
    assert "distinct camera" in p
    # 顶部不再压制视角（原 "keep the eye-level ground view / no bird's-eye" 已删除）
    assert "every panel is a human-eye camera" not in p


def test_classify_space_default_outdoor_on_no_evidence():
    # 2026-08-30+ 修复：无室内/室外证据时默认 outdoor（place），避免封面户外庭院被拽成室内 room。
    assert _classify_space("深夜雨夜荒郊古寺，细雨薄雾，枯树残垣") == "outdoor"
    # 户外古建/庭院（含封面示例词）归 outdoor
    assert _classify_space("古寺回廊，细雨穿廊", "lanruo temple corridor in the rain") == "outdoor"
    # 明确室内场景仍判 indoor
    assert _classify_space("一间温馨的书房，木书架，台灯") == "indoor"
    assert _classify_space("现代客厅，沙发电视") == "indoor"

