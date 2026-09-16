"""estimate_num_frames 帧数测试（2026-08-16 起：分镜时长由 LLM 按内容自动配置 5~15s）。

LLM 按剧本内容自动拆分分镜时长（最长 15 秒），视频帧数 = 分镜 duration×fps 的
8n+1 档位（5s→121、10s→241、15s→361）；LTX-2.5 / MiniMax H3 均支持该范围。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.video_service import _resolve_next_keyframe_url, estimate_num_frames


def _seg(duration: float | None = 5.0, dialogue_chars: int = 0, narration: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        duration=duration,
        dialogue_lines=(
            [{"speaker": "甲", "text": "长" * dialogue_chars}] if dialogue_chars else []
        ),
        narration=narration,
    )


def test_5s_duration_121_frames():
    # 5s 分镜 → 121 帧
    assert estimate_num_frames(_seg(5.0), 121, 24) == 121


def test_10s_duration_241_frames():
    # 10s 分镜 → 241 帧（LLM 按内容分配的长镜）
    assert estimate_num_frames(_seg(10.0), 121, 24) == 241


def test_15s_duration_361_frames():
    # 15s 分镜（最长档）→ 361 帧
    assert estimate_num_frames(_seg(15.0), 121, 24) == 361


def test_missing_duration_falls_back_5s():
    # 分镜无 duration → 兜底 5s = 121 帧
    assert estimate_num_frames(_seg(None), 121, 24) == 121


def test_3s_duration_rounds_to_73_frames():
    # 异常短值也按比例换算（LLM 落库时会被 clamp 到 5~15，此处仅换算）
    assert estimate_num_frames(_seg(3.0), 121, 24) == 73  # (3.0*24-1)/8 → n=9 → 73


def test_frame_rate_30():
    # 30fps 下 10s → 297 帧（8n+1）
    assert estimate_num_frames(_seg(10.0), 121, 30) == 297


# ─── P6 首尾帧：下一镜关键帧作尾帧 ────────────────────────────────


def _seg_mock(index: int = 1, episode_id: str = "ep1") -> SimpleNamespace:
    return SimpleNamespace(episode_id=episode_id, index=index)


def test_next_keyframe_used_as_last_frame():
    db = MagicMock()
    db.scalar.side_effect = [
        SimpleNamespace(id="seg2"),          # 下一镜
        SimpleNamespace(image_url="http://x/kf2.png"),  # 下一镜关键帧
    ]
    assert _resolve_next_keyframe_url(db, _seg_mock(1)) == "http://x/kf2.png"


def test_last_segment_no_next_returns_none():
    db = MagicMock()
    db.scalar.return_value = None
    assert _resolve_next_keyframe_url(db, _seg_mock(5)) is None


def test_next_segment_without_keyframe_returns_none():
    db = MagicMock()
    db.scalar.side_effect = [SimpleNamespace(id="seg2"), None]
    assert _resolve_next_keyframe_url(db, _seg_mock(1)) is None
