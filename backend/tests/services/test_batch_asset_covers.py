"""P7 批量生成资产封面服务测试。

验证 batch_asset_covers：
- 只对无封面（cover_url 为空）的资产派发子任务，跳过已有封面
- 无目标时抛 ValueError
- 父任务（batch_asset_covers）+ 编排任务派发
- 支持按类型过滤
"""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.asset import AssetType
from app.models.task import TaskType
from app.services import asset_service, batch_service


def _asset(cover_url=None, atype="character"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        type=SimpleNamespace(value=atype),
        cover_url=cover_url,
        created_at=__import__("datetime").datetime.now(),
    )


def _setup_db(assets):
    db = MagicMock()
    db.scalars.return_value.all.return_value = assets
    return db


def test_batch_only_no_cover_assets(monkeypatch):
    """只派发无封面资产，跳过已有封面。"""
    a1 = _asset(cover_url=None)
    a2 = _asset(cover_url="http://x/1.png")
    a3 = _asset(cover_url=None)
    db = _setup_db([a1, a2, a3])

    dispatched = []
    def fake_generate_cover(db_, asset_id, model_id=None):
        dispatched.append(asset_id)
        return None, SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(asset_service, "generate_cover", fake_generate_cover)

    import app.tasks.batch_asset_covers as batch_mod
    batch_task = MagicMock()
    monkeypatch.setattr(batch_mod, "batch_asset_covers", batch_task)

    task, total, sub_ids, target_ids = batch_service.batch_asset_covers(
        db, uuid.uuid4(), AssetType.character, None
    )

    assert total == 2
    assert dispatched == [a1.id, a3.id]  # 有封面的 a2 被跳过
    assert target_ids == [str(a1.id), str(a3.id)]
    assert len(sub_ids) == 2
    # 父任务类型 + 子任务 ID 序列化
    parent = [a.args[0] for a in db.add.call_args_list if a.args and a.args[0].__class__.__name__ == "Task"]
    assert len(parent) == 1
    assert parent[0].type == TaskType.batch_asset_covers
    assert json.loads(parent[0].provider_task_id) == sub_ids
    # 编排任务派发
    batch_task.delay.assert_called_once_with(str(parent[0].id))


def test_batch_no_target_raises(monkeypatch):
    """全部已有封面 → ValueError。"""
    db = _setup_db([_asset(cover_url="http://x/1.png"), _asset(cover_url="http://x/2.png")])
    with pytest.raises(ValueError, match="已全部有封面"):
        batch_service.batch_asset_covers(db, uuid.uuid4(), AssetType.character, None)


def test_batch_filters_by_type(monkeypatch):
    """按类型过滤：SQL 层 where 条件包含指定类型。"""
    db = MagicMock()
    db.scalars.return_value.all.return_value = [_asset(cover_url=None)]
    monkeypatch.setattr(
        asset_service, "generate_cover",
        lambda db_, asset_id, model_id=None: (None, SimpleNamespace(id=uuid.uuid4())),
    )
    import app.tasks.batch_asset_covers as batch_mod
    monkeypatch.setattr(batch_mod, "batch_asset_covers", MagicMock())

    batch_service.batch_asset_covers(db, uuid.uuid4(), AssetType.scene, None)

    stmt = db.scalars.call_args[0][0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "asset.type" in sql
    assert "'scene'" in sql
