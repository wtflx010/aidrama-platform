"""关键帧审核拦截自动重试测试：content_policy_violation 时同 prompt 重试可自愈。"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.media import MediaStatus
from app.models.task import TaskStatus
from app.providers.errors import ProviderError
from app.tasks.generate_keyframe import _is_content_policy, generate_keyframe


def _build_mocks(tmp_path, provider):
    kf_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    segment = SimpleNamespace(
        episode=SimpleNamespace(project=SimpleNamespace(aspect_ratio="16:9"))
    )
    kf = SimpleNamespace(
        id=kf_id, segment_id=str(uuid.uuid4()), segment=segment,
        prompt="a person standing", status=MediaStatus.pending,
        image_url=None, task_id=None,
    )
    task = SimpleNamespace(id=task_id, target_id=kf_id, model_id=str(uuid.uuid4()))
    db = MagicMock()

    def _get(model_cls, pk, *a, **kw):
        return {"Task": task, "Keyframe": kf, "Model": SimpleNamespace(id=task.model_id)}.get(
            model_cls.__name__
        )
    db.get.side_effect = _get
    return kf, task_id, db


def test_keyframe_retries_on_policy_violation(monkeypatch, tmp_path):
    """前 2 次被安全策略拦截 → 自动重试，第 3 次成功。"""
    import app.tasks.generate_keyframe as g

    provider = MagicMock()
    handle = SimpleNamespace(provider="openai_compatible", providerTaskId="t1", pollUrl=None)
    calls = {"n": 0}
    def _img2img(prompt, urls, opts):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("content_policy_violation")
        return handle
    provider.imageToImage.side_effect = _img2img

    kf, task_id, db = _build_mocks(tmp_path, provider)

    monkeypatch.setattr(g, "SessionLocal", lambda: db)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.prompt_enhance_service.ensure_enhanced_prompt",
        lambda *a, **k: ("prompt", "negative"),
    )
    monkeypatch.setattr(g, "run_with_polling", lambda *a, **k: SimpleNamespace(imageUrls=["http://cdn/x.png"]))
    monkeypatch.setattr(g, "download_to_local", lambda *a, **k: "http://local/frame.png")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    generate_keyframe(task_id, ref_image_urls=["http://ref/1.png"])

    assert calls["n"] == 3  # 前 2 次拦截 + 第 3 次成功
    assert kf.status == MediaStatus.succeeded
    assert kf.image_url == "http://local/frame.png"


def test_keyframe_raises_after_max_retries(monkeypatch, tmp_path):
    """连续 3 次都被拦截 → 任务失败，错误保留。"""
    import app.tasks.generate_keyframe as g

    provider = MagicMock()
    provider.imageToImage.side_effect = ProviderError("content_policy_violation")

    kf, task_id, db = _build_mocks(tmp_path, provider)
    monkeypatch.setattr(g, "SessionLocal", lambda: db)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.prompt_enhance_service.ensure_enhanced_prompt",
        lambda *a, **k: ("prompt", "negative"),
    )
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    generate_keyframe(task_id, ref_image_urls=["http://ref/1.png"])

    assert provider.imageToImage.call_count == 3
    assert kf.status == MediaStatus.failed
    assert kf.error  # 保留原始错误信息（ProviderError 原文）


def test_keyframe_non_policy_error_not_retried(monkeypatch, tmp_path):
    """非审核错误（如模型 500）不重试，立即失败。"""
    import app.tasks.generate_keyframe as g

    provider = MagicMock()
    provider.imageToImage.side_effect = ProviderError("HTTP 500 internal error")

    kf, task_id, db = _build_mocks(tmp_path, provider)
    monkeypatch.setattr(g, "SessionLocal", lambda: db)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.prompt_enhance_service.ensure_enhanced_prompt",
        lambda *a, **k: ("prompt", "negative"),
    )

    generate_keyframe(task_id, ref_image_urls=["http://ref/1.png"])

    assert provider.imageToImage.call_count == 1  # 不重试
    assert kf.status == MediaStatus.failed


def test_is_content_policy_matches():
    assert _is_content_policy(ProviderError("content_policy_violation"))
    assert _is_content_policy(ProviderError("Content safety filter triggered"))
    assert _is_content_policy(ProviderError("内容被模型安全策略拦截"))
    assert not _is_content_policy(ProviderError("HTTP 500 internal error"))


def test_is_content_policy_matches_httpstatus_error_body():
    """HTTP 400 拦截：关键词在响应体 body，str(异常) 不含关键词（历史 bug）。"""
    import httpx

    req = httpx.Request("POST", "https://apihub.agnes-ai.cn/v1/images/generations")
    resp = httpx.Response(
        400,
        request=req,
        json={"error": {"message": "content_policy_violation: unsafe content"}},
    )
    err = httpx.HTTPStatusError("Client error '400 Bad Request'", request=req, response=resp)
    # str(异常) 只有 "Client error '400 Bad Request'..."，不含关键词
    assert "content_policy" not in str(err).lower()
    # 但 is_content_policy 应命中响应体
    assert _is_content_policy(err)


def test_keyframe_retries_on_httpstatus_error_policy(monkeypatch, tmp_path):
    """HTTPStatusError 响应体含审核关键词时也应自动重试（复现线上 400 拦截 bug）。"""
    import httpx
    import app.tasks.generate_keyframe as g

    provider = MagicMock()
    handle = SimpleNamespace(provider="openai_compatible", providerTaskId="t1", pollUrl=None)
    calls = {"n": 0}

    def _make_err():
        req = httpx.Request("POST", "https://apihub.agnes-ai.cn/v1/images/generations")
        resp = httpx.Response(
            400, request=req,
            json={"error": {"message": "content_policy_violation: unsafe content"}},
        )
        return httpx.HTTPStatusError("Client error '400 Bad Request'", request=req, response=resp)

    def _img2img(prompt, urls, opts):
        calls["n"] += 1
        if calls["n"] < 2:
            raise _make_err()
        return handle
    provider.imageToImage.side_effect = _img2img

    kf, task_id, db = _build_mocks(tmp_path, provider)
    monkeypatch.setattr(g, "SessionLocal", lambda: db)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.prompt_enhance_service.ensure_enhanced_prompt",
        lambda *a, **k: ("prompt", "negative"),
    )
    monkeypatch.setattr(g, "run_with_polling", lambda *a, **k: SimpleNamespace(imageUrls=["http://cdn/x.png"]))
    monkeypatch.setattr(g, "download_to_local", lambda *a, **k: "http://local/frame.png")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    generate_keyframe(task_id, ref_image_urls=["http://ref/1.png"])

    assert calls["n"] == 2  # 第 1 次 HTTPStatusError 拦截 + 第 2 次成功
    assert kf.status == MediaStatus.succeeded
