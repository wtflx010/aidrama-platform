"""导演台模式单元测试（2026-08-29）：timeline 组装 / 帧网格 / 分辨率 / 模板结构。"""
import json

from app.providers.comfyui_templates import _build_minimax_h3_director_template
from app.services.canvas_director_service import (
    _resolve_dims,
    _snap_h3_frames,
    build_director_timeline,
)


class FakeSeg:
    duration = 5.0


def _rows(n=3):
    return [
        (FakeSeg(), 5.0 + i, "镜 %d 的动作描述" % (i + 1), i > 0)
        for i in range(n)
    ]


def test_snap_h3_frames():
    assert _snap_h3_frames(124) == 124  # 官方 17n+5 网格上已是格点
    assert _snap_h3_frames(121) == 107  # 121 → 下取到 17*6+5
    assert _snap_h3_frames(10) == 5  # 下限为 5
    assert _snap_h3_frames(0) == 124  # 无效输入落到默认
    assert _snap_h3_frames(500) == 362  # 上限钳制（2026-08-30）


def test_resolve_dims():
    assert _resolve_dims("16:9", "768p") == (1344, 768, 1344)
    assert _resolve_dims("9:16", "480p") == (480, 832, 832)
    assert _resolve_dims("1:1", "768p") == (768, 768, 768)
    assert _resolve_dims("16:9", "4k") == (1344, 768, 1344)  # 未知档位回退 768p


def test_build_director_timeline_segments_and_continuity():
    tl = json.loads(
        build_director_timeline(
            _rows(3),
            global_refs=[{"index": 0, "imageFile": "http://x/a.png", "label": "L"}],
            global_prompt="GP", task_type_value="r2v — 参考主体生视频(Reference to Video)",
            fps=24, width=1344, height=768, ref_max_size=1344,
            context_enabled=True, context_frames=22,
        )
    )
    segs = tl["segments"]
    assert [s["start"] for s in segs] == [0, 107, 248]  # 5s/6s/7s → 107/141/158
    assert tl["totalFrames"] == 406
    assert tl["output"]["continuityEnabled"] is True
    assert tl["output"]["continuityOverlapFrames"] == 22
    assert segs[0]["fromPrev"] is False  # 首段不接上段
    assert segs[1]["fromPrev"] is True
    assert tl["global"]["commonEnabled"] is True
    assert tl["global"]["refs"][0]["imageFile"] == "http://x/a.png"


def test_build_director_timeline_continuity_off():
    tl = json.loads(
        build_director_timeline(
            _rows(2),
            global_refs=[], global_prompt="", task_type_value="r2v — 参考主体生视频(Reference to Video)",
            fps=24, width=832, height=480, ref_max_size=864,
            context_enabled=False, context_frames=0,
        )
    )
    assert tl["output"]["continuityEnabled"] is False
    assert tl["output"]["continuityOverlapFrames"] == 0


def test_director_template_structure():
    t = _build_minimax_h3_director_template()
    assert t["5"]["class_type"] == "MiniMaxH3Director"
    ins = t["5"]["inputs"]
    for key in (
        "timeline_data", "task_type", "global_prompt", "steps", "sampler",
        "scheduler", "cfg", "seed", "shift_video", "shift_audio",
        "clear_vram_between_segments", "width", "height", "ref_max_size", "total_frames",
    ):
        assert key in ins
    assert t["7"]["class_type"] == "SaveVideo"
    cv = t["6"]["inputs"]
    assert cv["images"] == ["5", 0]
    assert cv["audio"] == ["5", 1]
