"""成片转场时长自适应规则测试（P5 九宫格运镜视觉接续）。"""
from app.models.segment import Segment
from app.tasks.generate_export import _transition_fade_secs


def _seg(shot_type=None, emotion=None):
    return Segment(shot_type=shot_type, emotion=emotion)


def test_ep_boundary_prefers_06():
    """幕间转场固定 0.6s（情绪紧张也不改，幕切换以淡入淡出为主）。"""
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "紧张"), True) == 0.6


def test_tense_emotion_fast_cut():
    """后镜情绪紧张 → 0.2s 近硬切增强冲击。"""
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "紧张"), False) == 0.2
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "愤怒"), False) == 0.2


def test_shot_level_jump_fast_cut():
    """景别向前跳级（远景→特写级别差 4，拉近冲击）→ 0.2s 近硬切。"""
    assert _transition_fade_secs(_seg("远景"), _seg("特写", "平静"), False) == 0.2
    assert _transition_fade_secs(_seg("中景"), _seg("特写", "欢快"), False) == 0.2


def test_shot_pull_back_no_hard_cut():
    """景别向后回拉（特写→中景，情绪舒缓）→ 不硬切，0.6s 慢叠化。"""
    assert _transition_fade_secs(_seg("特写"), _seg("中景", "温馨"), False) == 0.6
    assert _transition_fade_secs(_seg("特写"), _seg("全景", "平静"), False) == 0.6


def test_soft_emotion_slow_fade():
    """后镜情绪舒缓收束（温馨/平静/悲伤）→ 0.6s 慢叠化。"""
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "温馨"), False) == 0.6
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "平静"), False) == 0.6


def test_same_level_default_03():
    """同级景别连续叙事 → 0.3s 叠化；无情绪/未知情绪同样 0.3s。"""
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "欢快"), False) == 0.3
    assert _transition_fade_secs(_seg("中景"), _seg("中景", None), False) == 0.3
    assert _transition_fade_secs(_seg("中景"), _seg("中景", "史诗"), False) == 0.3


def test_missing_segments_graceful():
    """seg 缺失（None）→ 不报错，回退 0.3s。"""
    assert _transition_fade_secs(None, None, False) == 0.3
    assert _transition_fade_secs(None, _seg("中景", "欢快"), False) == 0.3
