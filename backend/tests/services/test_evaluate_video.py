from app.services.evaluate_service import _sample_video_frames, _video_to_local, rule_scores


def _seg(**kw):
    base = {"index": 1, "shot_type": "中景", "camera": "push in", "emotion": "愤怒",
            "description": "角色愤怒拍桌", "dialogue": "林浅：你敢", "subtitle": "",
            "enhanced_prompt": "push in, angry"}
    base.update(kw)
    return base


def test_sample_frames_none_returns_empty():
    assert _sample_video_frames(None) == []


def test_sample_frames_external_url_returns_empty():
    # 非本地媒体 URL 不抽帧，调用方据此回退纯文本评分
    assert _sample_video_frames("https://cdn.example.com/v.mp4") == []


def test_video_to_local_maps_static_media():
    path = _video_to_local("http://localhost:8000/static/media/videos/abc/clip.mp4")
    assert path is not None
    assert path.endswith("videos/abc/clip.mp4")


def test_video_to_local_external_returns_none():
    assert _video_to_local("https://cdn.example.com/v.mp4") is None
    assert _video_to_local(None) is None


def test_rule_scores_basic_shape():
    scores = rule_scores([_seg(index=1)])
    for k in ("hook", "attention", "retention", "virality", "overall"):
        assert k in scores and 1 <= scores[k] <= 10
    assert scores["details"]["segments"] == 1


def test_rule_scores_empty():
    assert rule_scores([])["overall"] == 5
