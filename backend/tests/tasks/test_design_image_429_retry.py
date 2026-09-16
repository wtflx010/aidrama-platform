"""幕级设计图 429 限流自动重试测试（2026-08-07 修复）。"""
import httpx
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.providers.errors import ProviderError
from app.tasks.generate_episode_video import _generate_design_image


def _make_429():
    req = httpx.Request("POST", "https://apihub.agnes-ai.cn/v1/images/generations")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rate limit exceeded"}})
    return httpx.HTTPStatusError("Client error '429 Too Many Requests'", request=req, response=resp)


def test_design_image_retries_on_429(monkeypatch):
    """前 2 次 429 限流 → 退避重试，第 3 次成功。"""
    import app.tasks.generate_episode_video as g

    model = SimpleNamespace(id="m1")
    provider = MagicMock()
    handle = SimpleNamespace(provider="openai_compatible", providerTaskId="t1", pollUrl=None)
    calls = {"n": 0}

    def _img2img(prompt, refs, opts):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _make_429()
        return handle
    provider.imageToImage.side_effect = _img2img

    monkeypatch.setattr(g, "_resolve_model", lambda *a, **k: model)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "run_with_polling", lambda *a, **k: SimpleNamespace(imageUrls=["http://cdn/x.png"]))
    monkeypatch.setattr(g, "download_to_local", lambda *a, **k: "http://local/design.png")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    url = _generate_design_image(MagicMock(), "task1", "ep1", "seg1_first",
                                 "prompt", ["http://ref/1.png"], "16:9", "neg")

    assert calls["n"] == 3  # 2 次限流 + 第 3 次成功
    assert url == "http://local/design.png"


def test_design_image_fails_after_max_429(monkeypatch):
    """连续 3 次 429 → 抛出限流错误，不无限重试。"""
    import app.tasks.generate_episode_video as g

    model = SimpleNamespace(id="m1")
    provider = MagicMock()
    provider.imageToImage.side_effect = _make_429()

    monkeypatch.setattr(g, "_resolve_model", lambda *a, **k: model)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    with pytest.raises(httpx.HTTPStatusError):
        _generate_design_image(MagicMock(), "task1", "ep1", "seg1_first",
                               "prompt", ["http://ref/1.png"], "16:9", "neg")
    assert provider.imageToImage.call_count == 3


def test_design_image_retries_on_policy_then_429(monkeypatch):
    """审核拦截 + 429 混合场景：两类重试都生效。"""
    import app.tasks.generate_episode_video as g

    model = SimpleNamespace(id="m1")
    provider = MagicMock()
    handle = SimpleNamespace(provider="openai_compatible", providerTaskId="t1", pollUrl=None)
    calls = {"n": 0}

    def _img2img(prompt, refs, opts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("content_policy_violation")
        if calls["n"] == 2:
            raise _make_429()
        return handle
    provider.imageToImage.side_effect = _img2img

    monkeypatch.setattr(g, "_resolve_model", lambda *a, **k: model)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "run_with_polling", lambda *a, **k: SimpleNamespace(imageUrls=["http://cdn/x.png"]))
    monkeypatch.setattr(g, "download_to_local", lambda *a, **k: "http://local/design.png")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    url = _generate_design_image(MagicMock(), "task1", "ep1", "seg1_first",
                                 "prompt", ["http://ref/1.png"], "16:9", "neg")

    assert calls["n"] == 3
    assert url == "http://local/design.png"
