"""AI 漫剧 · 全站共享常量与小幅工具函数（2026-09-17 新增）。

用于收敛此前散落在多个 service/task 里的同类常量，避免"两个语速常数 / 两套帧数网格"
长期漂移：
- CHARS_PER_SEC：中文真实对话朗读语速（字/秒）。LLM 拆分台词分镜、字幕时长估算、
  配音时长探测回退、连续长片口播容量均以此为唯一权威值（历史曾出现 5.0 / 5.5 / 4 三种）。
- snap_h3_frames：MiniMax H3 的 17n+5 帧数网格，单镜链路与导演台/连续长片共用，
  消除"单镜先 8n+1、provider 再 17n+5"的二次对齐漂移。
"""
from __future__ import annotations

# 中文真实对话语速（实测约 5~6 字/秒，2026-08-10 由 4 字/秒上调至 5.5 字/秒）
CHARS_PER_SEC = 5.5

# H3 单段帧数上限（与单镜链路 frames_max 对齐，防时长越界/爆显存）
H3_FRAMES_MAX = 362
H3_FRAMES_MIN = 5
# H3 单段时长上限（秒）
H3_DURATION_MAX_SEC = 15.0


def snap_h3_frames(frames: int, *, max_frames: int = H3_FRAMES_MAX) -> int:
    """落到 MiniMax H3 的 17n+5 帧数网格（与 provider 帧数对齐），带上限/下限。

    幂等：输入已在网格上则原样返回，因此单镜链路可直接按此精确换算，
    无需再经 provider 二次对齐。
    """
    frames = int(frames or 0)
    if frames <= 0:
        return 124  # 5 秒 @24fps 的 H3 网格值
    snapped = max(H3_FRAMES_MIN, (frames - 5) // 17 * 17 + 5)
    return min(max_frames, snapped)
