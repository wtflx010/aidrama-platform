"""四视图任务测试：正面=封面、特写=封面裁头部、侧面/背面独立 img2img 生成。

four_view_urls = [正面, 侧面, 背面, 特写]：
- 正面(index0) = 封面本身（cover_url）
- 侧面/背面 = 各一次独立 imageToImage（索引 1/2）
- 特写(index3) = 封面裁头部区域
"""
import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from app.models.asset import AssetType
from app.models.media import MediaStatus
from app.tasks.generate_asset_fourview import (
    CONSISTENCY,
    _VIEW_PREFIX,
    generate_asset_fourview,
)


def _build_env(monkeypatch, tmp_path):
    """构造任务环境（asset/task/project/model/db mocks），返回各对象。"""
    asset_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    asset = SimpleNamespace(
        id=asset_id, type=AssetType.character, name="测试角色",
        cover_url="http://local/cover.png", four_view_urls=[],
        status=MediaStatus.pending, task_id=None, error=None,
        project_id=str(uuid.uuid4()), expanded_description="一位身穿古装的年轻男子",
        description="古装男子",
    )
    task = SimpleNamespace(id=task_id, target_id=asset_id, model_id=str(uuid.uuid4()))
    project = SimpleNamespace(id=asset.project_id, style_id=None, art_style_prompt=None)
    model = SimpleNamespace(id=task.model_id)

    db = MagicMock()
    def _get(model_cls, pk, *a, **kw):
        name = model_cls.__name__
        if name == "Task":
            return task
        if name == "Asset":
            return asset
        if name == "Project":
            return project
        if name == "Model":
            return model
        return None
    db.get.side_effect = _get
    monkeypatch.setattr("app.tasks.generate_asset_fourview.SessionLocal", lambda: db)
    monkeypatch.setattr(
        "app.services.style_service.get_effective_style_prompt", lambda *a, **k: ""
    )
    monkeypatch.setattr(
        "app.services.style_service.is_realistic_style", lambda *a, **k: False
    )
    return asset, task, db


def _write_cover(path: str, size: int = 256):
    """写一张封面：白底 + 上部中央黑块（可验证特写=封面裁头部）。"""
    im = Image.new("L", (size, size), 255)
    for x in range(int(size * 0.35), int(size * 0.65)):
        for y in range(int(size * 0.1), int(size * 0.3)):
            im.putpixel((x, y), 0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)
    return im


def _write_view(path: str, size: int = 128):
    """写一张侧面/背面生成图：白底 + 中央黑块。"""
    im = Image.new("L", (size, size), 255)
    for x in range(int(size * 0.35), int(size * 0.65)):
        for y in range(int(size * 0.35), int(size * 0.65)):
            im.putpixel((x, y), 0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)
    return im


def test_fourview_all_views_generated(monkeypatch, tmp_path):
    """四张视图（正面/侧面/背面/特写）全部走 img2img 独立生成；正面刷新封面。

    2026-08-07 起四视图统一同一 img2img 管线（此前正面复用封面、特写裁头部），
    正面（index 0）生成结果覆盖 cover_url。
    """
    asset, task, db = _build_env(monkeypatch, tmp_path)
    provider = MagicMock()
    seen = []
    def _img2img(prompt, urls, opts):
        seen.append(prompt)
        return SimpleNamespace()
    provider.imageToImage.side_effect = _img2img
    provider.getTaskResult.side_effect = lambda h: SimpleNamespace(
        imageUrls=["http://cdn/view.png"]
    )
    monkeypatch.setattr(
        "app.tasks.generate_asset_fourview.ProviderRegistry.for_model", lambda m: provider
    )
    # run_with_polling：直接返回生成图，避免真实轮询访问 mock 对象缺失的 status 字段
    monkeypatch.setattr(
        "app.tasks.generate_asset_fourview.run_with_polling",
        lambda *a, **k: SimpleNamespace(imageUrls=["http://cdn/view.png"]),
    )

    # download_to_local：生成图落盘为 cover.png（正面）/ view_1~3.png（侧面/背面/特写）
    def _download(url, subdir, filename, task_id=None, heartbeat_interval=20):
        path = os.path.join(str(tmp_path), "assets", asset.id, filename)
        if filename == "cover.png":
            _write_cover(path)
        else:
            _write_view(path)
        return f"http://local/{subdir}/{filename}"
    monkeypatch.setattr("app.tasks.generate_asset_fourview.download_to_local", _download)
    monkeypatch.setattr("app.tasks.generate_asset_fourview.update_task", lambda *a, **k: None)

    generate_asset_fourview(task.id)

    # 四张视图全部独立生成
    assert len(seen) == 4, f"应生成 4 张视图，实际 {len(seen)}"
    for i, p in enumerate(seen):
        assert p.startswith(_VIEW_PREFIX[i]), f"view_{i} prompt 应以视角前缀开头"
        assert CONSISTENCY.split(",")[0] in p, f"view_{i} prompt 应含一致性约束"
    # 落库成功：4 张分图；正面(index0) 生成结果刷新封面
    assert asset.status == MediaStatus.succeeded
    assert len(asset.four_view_urls) == 4
    assert asset.four_view_urls[0] == asset.cover_url, "正面视图生成后刷新封面"
    assert "view_1.png" in asset.four_view_urls[1]
    assert "view_2.png" in asset.four_view_urls[2]
    assert "view_3.png" in asset.four_view_urls[3]
    # 不做单画布整合图（sheet 已废弃）
    assert not hasattr(asset, "sheet_url")
    assert not os.path.exists(os.path.join(str(tmp_path), "assets", asset.id, "sheet.png"))
    db.commit.assert_called()


def test_fourview_missing_cover_fails(monkeypatch, tmp_path):
    """封面缺失 → 直接失败，不调用 provider。"""
    asset, task, db = _build_env(monkeypatch, tmp_path)
    asset.cover_url = None
    provider = MagicMock()
    monkeypatch.setattr(
        "app.tasks.generate_asset_fourview.ProviderRegistry.for_model", lambda m: provider
    )
    monkeypatch.setattr("app.tasks.generate_asset_fourview.update_task", lambda *a, **k: None)

    generate_asset_fourview(task.id)

    provider.imageToImage.assert_not_called()
    assert asset.status == MediaStatus.failed
    assert "封面缺失" in (asset.error or "")
