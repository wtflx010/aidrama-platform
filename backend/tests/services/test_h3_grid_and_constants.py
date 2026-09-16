"""帧数网格与语速常数统一（2026-09-17）。

背景：单镜链路曾先用 8n+1（121/241/361）估算、再等 provider 二次对齐到 H3 的
17n+5 网格；导演台/连续长片则直接 17n+5。排查中发现"两套帧数网格 + 多个语速常数
（4 / 5.0 / 5.5）"长期漂移。本用例验证：
- snap_h3_frames 是唯一 H3 17n+5 网格实现（幂等、带 362 上限）；
- 单镜估算帧（8n+1）经 H3 网格对齐后与导演台直接落网格**完全一致**（无二次漂移）；
- CHARS_PER_SEC 统一为 5.5（与 llm_script/video_service 的权威值一致）。
"""
from types import SimpleNamespace

from app.constants import CHARS_PER_SEC, H3_FRAMES_MAX, snap_h3_frames
from app.services.canvas_director_service import _snap_h3_frames
from app.services.video_service import estimate_num_frames


def _seg(duration: float | None = 5.0) -> SimpleNamespace:
    return SimpleNamespace(duration=duration)


def test_chars_per_sec_is_55():
    # 语速常数为唯一权威 5.5 字/秒
    assert CHARS_PER_SEC == 5.5


def test_snap_h3_frames_grid_values():
    # 17n+5 网格关键值（24fps）
    assert snap_h3_frames(120) == 107   # 5s
    assert snap_h3_frames(240) == 226   # 10s
    assert snap_h3_frames(360) == 345   # 15s


def test_snap_h3_frames_idempotent():
    # 已在网格上的值原样返回（不重复二次对齐）
    assert snap_h3_frames(107) == 107
    assert snap_h3_frames(226) == 226
    assert snap_h3_frames(345) == 345


def test_snap_h3_frames_upper_bound():
    # 上限 362（与单镜 frames_max 对齐）
    assert snap_h3_frames(100_000) == H3_FRAMES_MAX
    assert H3_FRAMES_MAX == 362


def test_snap_h3_frames_min_padded():
    # 极小/非法输入回落到最小 5 帧
    assert snap_h3_frames(0) == 124   # 0 走默认 5s@24 网格值
    assert snap_h3_frames(3) >= 5


def test_single_shot_estimate_aligns_with_director_grid():
    # 关键一致性：单镜链路估算帧（8n+1）经 H3 网格对齐后，
    # 与导演台/连续长片直接按 duration×fps 落网格**结果一致**（无二次漂移）。
    for dur in range(5, 16):
        est = estimate_num_frames(_seg(float(dur)), 121, 24)   # 8n+1 估算
        final_single = snap_h3_frames(est)                     # provider 对齐到 H3
        final_director = _snap_h3_frames(int(round(dur * 24))) # 导演台直接落网格
        assert final_single == final_director, (dur, final_single, final_director)


def test_director_snap_uses_shared_helper():
    # 导演台 _snap_h3_frames 已收敛为共享 snap_h3_frames，行为一致
    assert _snap_h3_frames(240) == snap_h3_frames(240)
    assert _snap_h3_frames(240) == 226
