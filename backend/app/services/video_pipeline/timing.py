"""时间轴/切点原语：由各分镜帧数换算整片分段切点（连续长片裁回分镜用）。

收敛自 project_director_generate._crop_film_to_segments 的纯切点计算；
ffmpeg 裁片/回写等任务侧逻辑保留在任务内。
"""
from __future__ import annotations


def timeline_cuts(frames: list[int], fps: float = 24.0) -> list[dict]:
    """frames: 各分镜帧数列表（顺序=时间顺序）。返回按累积起始/结束时间(秒)的切点。"""
    cuts: list[dict] = []
    cur = 0
    fps = max(1.0, float(fps or 24.0))
    for i, length in enumerate(frames):
        start_sec = cur / fps
        end_sec = (cur + length) / fps
        cuts.append({"idx": i, "length": length, "start_sec": round(start_sec, 3), "end_sec": round(end_sec, 3)})
        cur += length
    return cuts