"""画布生成方案体系单测（2026-08-29）：注册表元数据 + 分发。"""
import types

from app.services import canvas_schemes
from app.services.canvas_schemes import SCHEMES, generate, list_schemes


def test_list_schemes_contains_single_and_director():
    metas = list_schemes()
    keys = [m["key"] for m in metas]
    assert {"single", "director", "director_text"} <= set(keys)
    m = next(x for x in metas if x["key"] == "director")
    assert m["label"] == "多段连拍·整片"
    assert m["input_kind"] == "director-segments"
    m1 = next(x for x in metas if x["key"] == "single")
    assert m1["input_kind"] == "node-batch"
    mt = next(x for x in metas if x["key"] == "director_text")
    assert mt["label"] == "多段连拍·纯文生"
    assert mt["input_kind"] == "director-segments"


def test_generate_dispatches_unknown_scheme():
    payload = types.SimpleNamespace(scheme="nope", node_ids=[], kind="image")
    try:
        generate(None, None, payload)
        assert False, "应抛未知方案错误"
    except ValueError as exc:
        assert "未知方案" in str(exc)


def test_director_text_forces_t2v_and_disable_refs(monkeypatch):
    """director_text 方案：config 强制 t2v + 关闭公共参考图，再走导演台执行器。"""
    import types

    from app.schemas.canvas import DirectorGenerate
    from app.services.canvas_director_service import generate as director_generate

    captured = {}

    def _fake_gen(db, board_id, typed):
        captured["typed"] = typed
        task = types.SimpleNamespace(id="t9")
        return task, ["n1", "n2"], 240

    monkeypatch.setattr("app.services.canvas_director_service.generate", _fake_gen)

    payload = types.SimpleNamespace(
        scheme="director_text", node_ids=["n1", "n2"], shots=None,
        model_id=None, ratio=None, kind="video", config={"res": "768p", "ratio": "16:9"},
    )
    from app.services.canvas_schemes import generate

    task, node_ids, extra = generate(None, 1, payload)
    assert task.id == "t9" and node_ids == ["n1", "n2"]
    typed: DirectorGenerate = captured["typed"]
    assert typed.config["task_type"] == "t2v — 文生视频(Text to Video)"
    assert typed.config["enable_common_refs"] is False
    assert typed.config["res"] == "768p"  # 用户配置保留


def test_generate_registers_custom_scheme():
    seen = {}

    def _fake(db, board_id, payload):
        seen["called"] = payload.scheme
        task = types.SimpleNamespace(id="t1")
        return task, ["n1"], [{"note": "extra"}]

    SCHEMES["ut_fake"] = canvas_schemes.SchemeSpec(
        key="ut_fake", label="测试", input_kind="node-batch", generate_fn=_fake,
    )
    try:
        metas = list_schemes()
        assert any(x["key"] == "ut_fake" for x in metas)
        payload = types.SimpleNamespace(scheme="ut_fake", node_ids=["n1"], kind="video")
        task, node_ids, extra = generate(None, None, payload)
        assert task.id == "t1" and node_ids == ["n1"] and extra == [{"note": "extra"}]
        assert seen["called"] == "ut_fake"
    finally:
        SCHEMES.pop("ut_fake", None)
