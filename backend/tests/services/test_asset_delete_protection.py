"""回归测试：资产删除（2026-08-30 起绑定项目也可删除）。

规则：资产无论是否绑定项目均可删除；删除时清除 project_asset 绑定并清理媒体文件。
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.assets import AssetBatchDeleteBody
from app.services import asset_service

NONEXISTENT = uuid.uuid4()
OK_ID = uuid.uuid4()
BOUND_ID = uuid.uuid4()


class TestAssetDeleteService:
    """资产删除服务层：绑定保护逻辑。"""

    def test_delete_nonexistent_returns_false(self):
        """资产不存在 → 返回 False（不抛绑定错误）。"""
        from unittest.mock import MagicMock

        db = MagicMock()
        db.get.return_value = None
        assert asset_service.delete(db, NONEXISTENT) is False

    def test_delete_bound_asset_succeeds(self):
        """已绑定项目的资产 → 正常删除（2026-08-30 起绑定项目也可删除）。"""
        from unittest.mock import MagicMock

        db = MagicMock()
        db.get.return_value = MagicMock()
        assert asset_service.delete(db, BOUND_ID) is True
        db.delete.assert_called_once()
        db.commit.assert_called_once()

    def test_delete_unbound_asset_succeeds(self):
        """未绑定项目的全局资产 → 正常删除并提交。"""
        from unittest.mock import MagicMock, patch

        db = MagicMock()
        db.get.return_value = MagicMock()
        with patch("app.services.asset_service.project_ids_of", return_value=[]):
            assert asset_service.delete(db, OK_ID) is True
        db.delete.assert_called_once()
        db.commit.assert_called_once()


class TestAssetDeleteEndpoints:
    """资产删除端点：绑定保护在 HTTP 层的表现。"""

    def _db(self, ids):
        """Mock db：db.get 命中给定 id 集合并返回资产对象。"""
        from unittest.mock import MagicMock

        db = MagicMock()
        db.get.side_effect = lambda model, aid: MagicMock() if str(aid) in ids else None
        return db

    def test_delete_asset_bound_succeeds(self):
        """单个删除已绑定资产 → 正常返回 ok（2026-08-30 起绑定项目也可删除）。"""
        from unittest.mock import patch

        from app.api.v1 import assets as assets_api

        db = self._db([str(BOUND_ID)])
        with patch("app.api.v1.assets.asset_service.delete") as m:
            m.return_value = True
            resp = assets_api.delete_asset(BOUND_ID, db)
        assert resp == {"ok": True}

    def test_delete_asset_missing_returns_404(self):
        """删除不存在的资产 → HTTP 404。"""
        from app.api.v1 import assets as assets_api

        db = self._db([])
        with pytest.raises(HTTPException) as exc:
            assets_api.delete_asset(NONEXISTENT, db)
        assert exc.value.status_code == 404

    def test_delete_asset_unbound_succeeds(self):
        """单个删除未绑定资产 → 正常返回 ok。"""
        from unittest.mock import MagicMock, patch

        from app.api.v1 import assets as assets_api

        db = self._db([str(OK_ID)])
        with patch("app.api.v1.assets.asset_service.delete") as m:
            m.side_effect = lambda _db, aid: str(aid) == str(OK_ID)
            resp = assets_api.delete_asset(OK_ID, db)
        assert resp == {"ok": True}
        m.assert_called_once_with(db, OK_ID)

    def _bound_db_scalars(self, ids):
        from unittest.mock import MagicMock

        db = MagicMock()
        db.scalars.return_value.all.return_value = [uuid.UUID(i) for i in ids]
        return db

    def test_batch_delete_all_including_bound(self):
        """批量删除：绑定资产的资产也删除（2026-08-30 起绑定项目也可删除）。"""
        from unittest.mock import MagicMock, patch

        from app.api.v1 import assets as assets_api

        db = self._bound_db_scalars([str(OK_ID), str(BOUND_ID)])
        body = AssetBatchDeleteBody(asset_ids=[OK_ID, BOUND_ID])
        with patch("app.api.v1.assets.asset_service.delete") as m:
            m.side_effect = MagicMock(return_value=True)
            resp = assets_api.batch_delete_assets(body, db)
        assert resp["ok"] is True
        assert resp["deleted"] == 2
        assert resp["protected"] == []

    def test_delete_all_includes_bound(self):
        """一键删除全部：绑定资产的资产也删除（2026-08-30 起）。"""
        from unittest.mock import MagicMock, patch

        from app.api.v1 import assets as assets_api

        db = self._bound_db_scalars([str(OK_ID), str(BOUND_ID)])
        with patch("app.api.v1.assets.asset_service.delete") as m:
            m.side_effect = MagicMock(return_value=True)
            resp = assets_api.delete_all_assets(db)
        assert resp["ok"] is True
        assert resp["deleted"] == 2
        assert resp["protected"] == []
