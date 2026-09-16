"""超分目标尺寸与模型解析（2026-09-17）。

- target_dims：4x/2x 档均归一到 1080p 画布，偶数对齐（2 的倍数）。
- _resolve_upscale_model：优先启用 ComfyUI 视频模型，不再"任意取第一个"。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.model_config import ModelType, ProviderType
from app.services.upscale_service import target_dims, _resolve_upscale_model


def test_target_dims_landscape_even():
    # 横片按比例适配 1080p 画布（保纵横比、偶数对齐）
    w, h = target_dims(832, 480, "4x")
    assert w % 2 == 0 and h % 2 == 0
    # 保持源 832/480 纵横比（≈1.733）
    assert abs(w / h - 832 / 480) < 0.01
    # 高度不超 1080 上限
    assert h <= 1080


def test_target_dims_portrait_even():
    # 竖片按比例适配，纵横比保持
    w, h = target_dims(480, 832, "4x")
    assert w % 2 == 0 and h % 2 == 0
    assert abs(w / h - 480 / 832) < 0.01
    assert w <= 1080


def test_target_dims_2x_same_canvas_as_4x():
    # 2x 档与 4x 档落入同一 1080p 画布（画质标识一致）
    assert target_dims(832, 480, "2x") == target_dims(832, 480, "4x")


def test_target_dims_missing_returns_default():
    assert target_dims(0, 0) == (1920, 1080)


def test_resolve_upscale_model_prefers_video():
    # 视频型 ComfyUI 模型优先（SQL 以 (model_type==video).desc() 排序，rows 首位为视频模型）
    image_model = SimpleNamespace(model_type=ModelType.image)
    video_model = SimpleNamespace(model_type=ModelType.video)
    db = MagicMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [video_model, image_model])
    assert _resolve_upscale_model(db) is video_model


def test_resolve_upscale_model_falls_back_to_any():
    # 无视频模型时回退任意启用 ComfyUI 模型
    image_model = SimpleNamespace(model_type=ModelType.image)
    db = MagicMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [image_model])
    assert _resolve_upscale_model(db) is image_model


def test_resolve_upscale_model_raises_when_none():
    db = MagicMock()
    db.scalars.return_value = SimpleNamespace(all=lambda: [])
    try:
        _resolve_upscale_model(db)
        assert False, "应当抛 ValueError"
    except ValueError:
        pass
