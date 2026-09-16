"""成片导出 · 字幕回退时间轴（2026-09-17 补充覆盖）。

原测试集中在策略/换算，导出合成字幕与混音零覆盖。此处覆盖最易回归的
_fallback_episode_video_subs（无 TTS 配音、视频模型原生朗读时的字幕估算轴）：
- 按台词/旁白字数占比切分总时长，时间轴单调不减、末块延伸至段尾；
- 去除"人物："/"旁白："前缀只留正文。
"""
from types import SimpleNamespace

from app.tasks.generate_export import _fallback_episode_video_subs, _strip_sub_prefix


def _seg(dialogue=None, narration=None):
    return SimpleNamespace(dialogue_lines=dialogue or [], narration=narration or "")


def test_empty_segments_returns_empty():
    assert _fallback_episode_video_subs([], 5000) == []


def test_one_line_fills_full_duration():
    seg = _seg(dialogue=[{"speaker": "甲", "text": "你今天去哪儿"}])
    subs = _fallback_episode_video_subs([seg], 5000)
    assert len(subs) == 1
    s, e, text = subs[0]
    assert text == "你今天去哪儿"            # 已去说话人前缀
    assert s == 0 and e <= 5000


def test_multiple_lines_monotonic_and_reach_end():
    seg = _seg(dialogue=[
        {"speaker": "林浅", "text": "是谁在敲门"},
        {"speaker": "林浅", "text": "这么晚了会是谁"},
    ])
    subs = _fallback_episode_video_subs([seg], 8000)
    assert len(subs) == 2
    # 时间轴单调不减
    assert subs[0][0] <= subs[0][1] <= subs[1][0] <= subs[1][1]
    # 末块延伸到段尾（避免末尾黑字幕）
    assert subs[-1][1] >= 7990


def test_narration_labeled_without_prefix():
    seg = _seg(narration="窗外下起了雨")
    subs = _fallback_episode_video_subs([seg], 3000)
    assert subs and subs[0][2] == "窗外下起了雨"


def test_strip_sub_prefix_removes_speaker():
    assert _strip_sub_prefix("林浅", "林浅：你好") == "你好"
    assert _strip_sub_prefix("", "旁白：你好", is_narration=True) == "你好"
