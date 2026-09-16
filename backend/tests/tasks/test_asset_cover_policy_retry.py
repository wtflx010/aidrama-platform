"""资产封面审核拦截自动重试测试：content_policy_violation 时同 prompt 重试可自愈。"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.media import MediaStatus
from app.tasks.generate_asset_cover import generate_asset_cover


def _build_mocks():
    asset_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    project = SimpleNamespace(aspect_ratio="16:9")
    asset = SimpleNamespace(
        id=asset_id, project_id=str(uuid.uuid4()), type="scene", name="测试场景",
        description="空荡的大厅", expanded_description=None,
        cover_url=None, status=MediaStatus.pending,
    )
    task = SimpleNamespace(id=task_id, target_id=asset_id, model_id=str(uuid.uuid4()))
    db = MagicMock()

    def _get(model_cls, pk, *a, **kw):
        return {"Task": task, "Asset": asset, "Model": SimpleNamespace(id=task.model_id), "Project": project}.get(
            model_cls.__name__
        )
    db.get.side_effect = _get
    return asset, task_id, db


def test_cover_retries_on_policy_violation(monkeypatch):
    """前 1 次被安全策略拦截 → 自动重试，第 2 次成功。"""
    import httpx
    import app.tasks.generate_asset_cover as g

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

    def _t2i(prompt, opts):
        calls["n"] += 1
        if calls["n"] < 2:
            raise _make_err()
        return handle
    provider.textToImage.side_effect = _t2i

    asset, task_id, db = _build_mocks()
    monkeypatch.setattr(g, "SessionLocal", lambda: db)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(g, "run_with_polling", lambda *a, **k: SimpleNamespace(imageUrls=["http://cdn/x.png"]))
    monkeypatch.setattr(g, "download_to_local", lambda *a, **k: "http://local/cover.png")
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    monkeypatch.setattr("app.services.asset_service.expand_description", lambda *a, **k: "empty hall")
    monkeypatch.setattr(
        "app.services.style_service.get_effective_style_prompt", lambda *a, **k: None
    )
    monkeypatch.setattr("app.services.style_service.is_realistic_style", lambda *a, **k: True)

    generate_asset_cover(task_id)

    assert calls["n"] == 2
    assert asset.status == MediaStatus.succeeded
    assert asset.cover_url == "http://local/cover.png"


def test_cover_raises_after_max_retries(monkeypatch):
    """连续 3 次都被拦截 → 任务失败。"""
    import httpx
    import app.tasks.generate_asset_cover as g

    provider = MagicMock()

    def _make_err():
        req = httpx.Request("POST", "https://apihub.agnes-ai.cn/v1/images/generations")
        resp = httpx.Response(
            400, request=req,
            json={"error": {"message": "content_policy_violation: unsafe content"}},
        )
        return httpx.HTTPStatusError("Client error '400 Bad Request'", request=req, response=resp)

    def _t2i(prompt, opts):
        raise _make_err()
    provider.textToImage.side_effect = _t2i

    asset, task_id, db = _build_mocks()
    monkeypatch.setattr(g, "SessionLocal", lambda: db)
    monkeypatch.setattr(g.ProviderRegistry, "for_model", lambda m: provider)
    monkeypatch.setattr(g, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    monkeypatch.setattr("app.services.asset_service.expand_description", lambda *a, **k: "empty hall")
    monkeypatch.setattr(
        "app.services.style_service.get_effective_style_prompt", lambda *a, **k: None
    )
    monkeypatch.setattr("app.services.style_service.is_realistic_style", lambda *a, **k: True)

    generate_asset_cover(task_id)

    assert provider.textToImage.call_count == 3
    assert asset.status == MediaStatus.failed
