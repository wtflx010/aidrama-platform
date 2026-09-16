from types import SimpleNamespace
from app.services.video_pipeline.orchestrator import build_spec

def _seg(i, dur=5.0):
    return SimpleNamespace(id="s" + str(i), description="镜头" + str(i), duration=dur)

def test_build_spec_single():
    spec = build_spec([_seg(1, 5.0)], mode="single")
    assert spec["mode"] == "single"
    assert spec["segments"][0]["frames"] == 107  # 5s@24 -> H3 17n+5
    assert spec["timeline"] is None

def test_build_spec_continuous_timeline_cuts():
    spec = build_spec([_seg(1, 5.0), _seg(2, 10.0)], mode="continuous", fps=24)
    assert spec["timeline"] is not None
    cuts = spec["timeline"]["cuts"]
    assert len(cuts) == 2
    assert cuts[0]["start_sec"] == 0
    assert cuts[1]["start_sec"] > 0

def test_build_spec_prompt_fn():
    spec = build_spec([_seg(1)], mode="single", prompt_fn=lambda s: "P_" + s.description)
    assert spec["segments"][0]["prompt"] == "P_镜头1"