"""prev_tail I2V 分辨率档位选择测试（2026-09 修复分辨率不一致）。

H3 I2V 硬首帧要求宽高为 32 的倍数：720p 的 720/16=45 奇数无法 patchify，
故只有当项目/分镜有效分辨率为 480p/768p（安全档）时才沿用，否则回退模型档位。
"""
from app.tasks.generate_video import resolve_prev_tail_grade


def test_768p_project_keeps_768p():
    # 768p 项目：沿用项目档位，保证与同项目其他镜头一致
    assert resolve_prev_tail_grade("768p", "768p") == "768p"


def test_480p_project_keeps_480p():
    # 480p 项目：安全档，沿用项目档位（修复：此前误强制 768p）
    assert resolve_prev_tail_grade("480p", "768p") == "480p"


def test_720p_project_falls_back_to_model_grade():
    # 720p 无法被 H3 patchify，回退模型 minimax_res 档位
    assert resolve_prev_tail_grade("720p", "768p") == "768p"


def test_empty_resolution_falls_back_to_model_grade():
    assert resolve_prev_tail_grade("", None) == "0.7mp"  # 无模型档位→模型默认档


def test_unknown_model_grade_falls_back_to_768p():
    assert resolve_prev_tail_grade("720p", "1080p") == "0.7mp"


def test_lower_letter_case_normalized():
    assert resolve_prev_tail_grade(" 768P ", "768p") == "768p"
