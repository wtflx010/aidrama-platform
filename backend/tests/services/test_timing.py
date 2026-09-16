from app.services.video_pipeline.timing import timeline_cuts

def test_timeline_cuts_basic():
    cuts = timeline_cuts([107, 226], 24.0)
    assert len(cuts) == 2
    # 第一段 0~107/24s，第二段接续
    assert cuts[0]["start_sec"] == 0 and abs(cuts[0]["end_sec"] - 107/24) < 0.01
    assert abs(cuts[1]["start_sec"] - 107/24) < 0.01
    assert cuts[1]["end_sec"] > cuts[1]["start_sec"]

def test_timeline_cuts_empty():
    assert timeline_cuts([]) == []

def test_timeline_cuts_fps_default():
    # fps=24 默认；单段
    c = timeline_cuts([240])
    assert c[0]["end_sec"] == 10.0