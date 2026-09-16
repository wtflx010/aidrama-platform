from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.model_config import ModelType
from app.services.video_pipeline.frames import duration_to_frames
from app.services.video_pipeline.model import resolve_director_model, resolve_shot_video_model


def test_duration_to_frames_h3_grid():
    # H3 17n+5 网格：5s->107, 10s->226, 15s->345
    assert duration_to_frames(5, 24) == 107
    assert duration_to_frames(10, 24) == 226
    assert duration_to_frames(15, 24) == 345
    assert duration_to_frames(None, 24) >= 5


def test_shot_video_model_explicit_disabled_raises():
    m = SimpleNamespace(id="m1", is_enabled=False)
    db = MagicMock()
    db.get.return_value = m
    try:
        resolve_shot_video_model(db, "m1")
        assert False, "应当抛 ValueError"
    except ValueError:
        pass


def test_shot_video_model_falls_back_to_prefer_ref():
    ref = SimpleNamespace(id="r1", is_enabled=True)
    fallback = SimpleNamespace(id="f1", is_enabled=True)
    db = MagicMock()
    # 第一次 scalar 返回 minimax_ref 模型
    db.scalar.return_value = ref
    assert resolve_shot_video_model(db, None) is ref


def test_director_model_none_when_no_comfyui_minimax():
    other = SimpleNamespace(capability={"video_kind": "wan"}, is_enabled=True)
    db = MagicMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [other])
    assert resolve_director_model(db, None) is None


def test_director_model_picks_comfyui_minimax():
    good = SimpleNamespace(capability={"video_kind": "minimax_ref"})
    db = MagicMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [good])
    assert resolve_director_model(db, None) is good
