"""风格解析服务测试（P8 系统兜底默认写实）。

验证：
- 项目未选风格 → 返回 DEFAULT_REALISTIC_STYLE（不再返回 None）
- 项目选了预设风格 → 返回对应 prompt_fragment
- 项目自定义风格 → 返回自定义文本
- has_explicit_style 判断
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.style_service import (
    DEFAULT_REALISTIC_STYLE,
    get_effective_style_prompt,
    has_explicit_style,
    is_realistic_style,
)


def _project(style_id=None, art_style_prompt=None):
    return SimpleNamespace(style_id=style_id, art_style_prompt=art_style_prompt)


def test_no_style_falls_back_to_realistic():
    """项目未配置任何风格 → 系统兜底返回写实风格片段（不返回 None）。"""
    db = MagicMock()
    project = _project(style_id=None, art_style_prompt=None)

    result = get_effective_style_prompt(db, project)

    assert result == DEFAULT_REALISTIC_STYLE
    assert "hyper-realistic" in result
    db.get.assert_not_called()  # style_id 为空不查表


def test_no_project_falls_back_to_realistic():
    """项目为空（如删除后）→ 同样返回写实兜底。"""
    db = MagicMock()
    assert get_effective_style_prompt(db, None) == DEFAULT_REALISTIC_STYLE


def test_preset_style_wins():
    """选了预设风格 → 返回该风格的 prompt_fragment。"""
    db = MagicMock()
    style = SimpleNamespace(prompt_fragment="japanese anime style, cel shading")
    db.get.return_value = style
    project = _project(style_id="s1", art_style_prompt=None)

    assert get_effective_style_prompt(db, project) == "japanese anime style, cel shading"
    db.get.assert_called_once()


def test_custom_style_used():
    """自定义 art_style_prompt → 直接使用（覆盖兜底）。"""
    db = MagicMock()
    project = _project(style_id=None, art_style_prompt="cinematic film still, warm tone")

    assert get_effective_style_prompt(db, project) == "cinematic film still, warm tone"


def test_custom_style_blank_falls_back():
    """art_style_prompt 为空白字符串 → 视为未配置，走兜底。"""
    db = MagicMock()
    project = _project(style_id=None, art_style_prompt="   ")

    assert get_effective_style_prompt(db, project) == DEFAULT_REALISTIC_STYLE


def test_has_explicit_style():
    """has_explicit_style：仅 style_id / art_style_prompt 任一非空时为 True。"""
    assert has_explicit_style(_project(style_id="s1")) is True
    assert has_explicit_style(_project(art_style_prompt="写实")) is True
    assert has_explicit_style(_project(style_id=None, art_style_prompt=None)) is False
    assert has_explicit_style(_project(art_style_prompt="  ")) is False
    assert has_explicit_style(None) is False


def test_is_realistic_style_no_style_true():
    """未显式配置风格 → 系统兜底写实 → True（追加非真人排除词）。"""
    db = MagicMock()
    assert is_realistic_style(db, _project(style_id=None, art_style_prompt=None)) is True
    assert is_realistic_style(db, None) is True


def test_is_realistic_style_realistic_preset_true():
    """写实摄影/电影感/黑白 → True（追加非真人排除词，防动漫混出）。"""
    db = MagicMock()
    for name in ("写实摄影", "电影感", "黑白"):
        db.get.return_value = SimpleNamespace(name=name)
        assert is_realistic_style(db, _project(style_id="s1")) is True, name


def test_is_realistic_style_nonrealistic_preset_false():
    """动漫/水墨/3D渲染等非写实风格 → False（不追加，避免冲突）。"""
    db = MagicMock()
    for name in ("动漫", "水墨", "水彩", "油画", "3D渲染", "像素艺术", "赛博朋克"):
        db.get.return_value = SimpleNamespace(name=name)
        assert is_realistic_style(db, _project(style_id="s1")) is False, name


def test_is_realistic_style_custom_prompt():
    """自定义风格文本：含动漫/插画关键词 → 非写实；否则按写实兜底。"""
    db = MagicMock()
    assert is_realistic_style(db, _project(art_style_prompt="anime cel shading")) is False
    assert is_realistic_style(db, _project(art_style_prompt="hyper realistic photo")) is True
