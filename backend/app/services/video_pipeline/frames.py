"""时长→帧数原语：统一以 MiniMax H3 17n+5 网格为准（constants.snap_h3_frames）。

单镜链路用 8n+1 作「估算」，但成片前一律落 H3 网格；导演台/连续长片直接落 H3 网格。
本模块提供统一换算入口，收敛三处重复。
"""
from __future__ import annotations

from app.constants import H3_FRAMES_MAX, snap_h3_frames


def duration_to_frames(duration: float | None, fps: int = 24) -> int:
    """按 duration×fps 精确换算，并直接落到 H3 17n+5 网格（带上限）。"""
    fps = max(1, int(fps or 24))
    target = max(0.2, float(duration or 5.0)) * fps
    return snap_h3_frames(int(round(target)), max_frames=H3_FRAMES_MAX)
