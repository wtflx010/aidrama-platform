"""回归测试：项目删除级联。

Bug: 项目删除时 500 ForeignKeyViolation —— video_clip.task_id 等 nullable FK
缺少 ondelete=SET NULL，ORM 批量删 task 时 PG 拒绝（仍被 video_clip 引用）。

此测试验证模型 FK 定义包含 ondelete=SET NULL，防止回归。
"""
import inspect
import uuid

from app.models.asset import Asset
from app.models.media import Keyframe, VideoClip
from app.models.voice import VoiceLine
from app.models.project import Episode, Project
from app.models.segment import Segment
from app.models.task import Task


def _fk_columns_with_ondelete(model_cls):
    """提取模型中所有 ForeignKey 的列名→ondelete 映射。"""
    from sqlalchemy import ForeignKey

    result = {}
    for col in model_cls.__table__.columns:
        for fk in col.foreign_keys:
            result[col.name] = fk.ondelete
    return result


class TestProjectCascadeFKConstraints:
    """验证级联删除链上的 FK 约束配置正确。"""

    def test_project_episode_cascade(self):
        """Episode.project_id → Project: ondelete=CASCADE。"""
        fks = _fk_columns_with_ondelete(Episode)
        assert fks.get("project_id") == "CASCADE"

    def test_episode_segment_cascade(self):
        """Segment.episode_id → Episode: ondelete=CASCADE。"""
        fks = _fk_columns_with_ondelete(Segment)
        assert fks.get("episode_id") == "CASCADE"

    def test_segment_keyframe_cascade(self):
        """Keyframe.segment_id → Segment: ondelete=CASCADE。"""
        fks = _fk_columns_with_ondelete(Keyframe)
        assert fks.get("segment_id") == "CASCADE"

    def test_segment_video_clip_cascade(self):
        """VideoClip.segment_id → Segment: ondelete=CASCADE。"""
        fks = _fk_columns_with_ondelete(VideoClip)
        assert fks.get("segment_id") == "CASCADE"

    def test_segment_voice_line_cascade(self):
        """VoiceLine.segment_id → Segment: ondelete=CASCADE。"""
        fks = _fk_columns_with_ondelete(VoiceLine)
        assert fks.get("segment_id") == "CASCADE"

    def test_project_task_cascade(self):
        """Task.project_id → Project: ondelete=CASCADE。"""
        fks = _fk_columns_with_ondelete(Task)
        assert fks.get("project_id") == "CASCADE"

    def test_project_asset_set_null(self):
        """Asset.project_id → Project: ondelete=SET NULL。

        2026-08-22 全局资产库改造（迁移 0068）：asset.project_id 不再随项目
        CASCADE 删除；项目删除仅解绑（project_asset 行 CASCADE），资产保留。
        2026-08-24 项目专用美术资产起，服务层在项目删除时主动删除资产
        （见 delete_with_media），FK 层面仍保持 SET NULL 兼容历史数据。
        """
        fks = _fk_columns_with_ondelete(Asset)
        assert fks.get("project_id") == "SET NULL"


class TestSetNullFKConstraints:
    """验证 nullable 的 task_id / keyframe_id FK 使用 ondelete=SET NULL。

    这是项目删除 bug 的根因：这些 FK 原先无 ondelete（默认 NO ACTION），
    导致 ORM 批量删 task 时 PG 拒绝（仍被 video_clip/keyframe 等引用）。
    """

    def test_keyframe_task_id_set_null(self):
        fks = _fk_columns_with_ondelete(Keyframe)
        assert fks.get("task_id") == "SET NULL", (
            "keyframe.task_id 必须设 ondelete=SET NULL，否则删 task 时触发 FK 违约"
        )

    def test_video_clip_task_id_set_null(self):
        fks = _fk_columns_with_ondelete(VideoClip)
        assert fks.get("task_id") == "SET NULL", (
            "video_clip.task_id 必须设 ondelete=SET NULL，否则删 task 时触发 FK 违约"
        )

    def test_video_clip_keyframe_id_set_null(self):
        fks = _fk_columns_with_ondelete(VideoClip)
        assert fks.get("keyframe_id") == "SET NULL", (
            "video_clip.keyframe_id 必须设 ondelete=SET NULL，否则删 keyframe 时触发 FK 违约"
        )

    def test_voice_line_task_id_set_null(self):
        fks = _fk_columns_with_ondelete(VoiceLine)
        assert fks.get("task_id") == "SET NULL", (
            "voice_line.task_id 必须设 ondelete=SET NULL，否则删 task 时触发 FK 违约"
        )

    def test_asset_task_id_set_null(self):
        fks = _fk_columns_with_ondelete(Asset)
        assert fks.get("task_id") == "SET NULL", (
            "asset.task_id 必须设 ondelete=SET NULL，否则删 task 时触发 FK 违约"
        )


class TestProjectDeleteService:
    """验证 project_service.delete 级联删行 + 同步清理磁盘媒体文件。"""

    def test_delete_returns_false_for_nonexistent(self):
        """删除不存在的项目返回 False。"""
        from unittest.mock import MagicMock
        from app.services.project_service import delete

        db = MagicMock()
        db.get.return_value = None
        assert delete(db, "nonexistent-uuid") is False

    def test_delete_calls_db_delete_and_commit(self):
        """删除存在的项目：收集 URL → db.delete + commit → 清理磁盘文件。"""
        from unittest.mock import MagicMock, patch
        from app.services.project_service import delete

        db = MagicMock()
        project = MagicMock()
        project.episodes = []
        project.assets = []
        project.tasks = []
        db.get.return_value = project
        db.scalars.return_value.all.return_value = []  # BGM/SFX 查询

        with patch("app.utils.media.delete_media_file") as mock_del:
            result = delete(db, "some-uuid")

        assert result is True
        db.delete.assert_called_once_with(project)
        db.commit.assert_called_once()
        mock_del.assert_not_called()  # 无媒体文件时不应调用

    def test_delete_collects_and_cleans_media_files(self):
        """项目含关键帧/视频/配音/资产/成片时，磁盘文件同步删除。"""
        from unittest.mock import MagicMock, patch
        from app.services.project_service import delete

        db = MagicMock()
        # 构造项目关系链：episodes → segments → keyframes/videos/voice_lines
        kf = MagicMock()
        kf.image_url = "http://localhost:8000/static/media/keyframes/kf1/frame.png"
        video = MagicMock()
        video.video_url = "http://localhost:8000/static/media/videos/v1/clip.mp4"
        video.first_frame_url = "http://localhost:8000/static/media/keyframes/kf1/frame.png"
        video.last_frame_url = None
        vl = MagicMock()
        vl.audio_url = "http://localhost:8000/static/media/voicelines/vl1/voice.wav"
        seg = MagicMock()
        seg.keyframes = [kf]
        seg.videos = [video]
        seg.voice_lines = [vl]
        ep = MagicMock()
        ep.segments = [seg]
        asset = MagicMock()
        asset.cover_url = "http://localhost:8000/static/media/assets/a1/cover.png"
        asset.four_view_urls = ["http://localhost:8000/static/media/assets/a1/view_0.png"]
        asset.states = []
        asset.reference_images = []
        asset.art_versions = []
        task = MagicMock()
        task.result_url = "http://localhost:8000/static/exports/p1/film_x.mp4"
        project = MagicMock()
        project.episodes = [ep]
        project.assets = [asset]
        project.tasks = [task]
        db.get.return_value = project
        db.scalars.return_value.all.return_value = []  # 资产/BGM/SFX 查询

        with patch("app.utils.media.delete_media_file") as mock_del:
            delete(db, "some-uuid")

        # 项目自身的媒体 URL 应被删除（含重复的首帧 URL 去重）；
        # 资产文件由 delete_with_media 单独清理（见 test_delete_removes_owned_assets）
        urls = [c.args[0] for c in mock_del.call_args_list]
        assert "http://localhost:8000/static/media/keyframes/kf1/frame.png" in urls
        assert "http://localhost:8000/static/media/videos/v1/clip.mp4" in urls
        assert "http://localhost:8000/static/media/voicelines/vl1/voice.wav" in urls
        assert "http://localhost:8000/static/exports/p1/film_x.mp4" in urls
        # 去重：kf1/frame.png 只删一次
        assert urls.count("http://localhost:8000/static/media/keyframes/kf1/frame.png") == 1
        # 资产 URL 不应由 delete_media_file 处理（走 delete_with_media 的独立清理）
        assert not any(u and "assets/a1/" in u for u in urls)

    def test_delete_removes_owned_assets(self):
        """归属本项目的资产：调用 delete_with_media 整行+图片删除。"""
        from unittest.mock import MagicMock, patch
        from app.services.project_service import delete

        pid = uuid.uuid4()
        owned_id = uuid.uuid4()
        owned = MagicMock()
        owned.project_id = pid

        db = MagicMock()
        project = MagicMock()
        project.episodes = []
        project.tasks = []

        def _get(cls, pk):
            if cls is Project:
                return project
            if cls is Asset:
                return owned if pk == owned_id else None
            return None

        db.get.side_effect = _get
        # 首次（owned_ids）返回 [owned_id]；随后（bound_ids / BGM / SFX）返回空
        db.scalars.return_value.all.side_effect = [[owned_id], [], [], []]

        with patch("app.services.asset_service.delete_with_media") as mock_dm, \
             patch("os.path.isdir", return_value=False) as _isdir:
            result = delete(db, pid)

        assert result is True
        mock_dm.assert_called_once()
        assert str(mock_dm.call_args.args[1]) == str(owned_id)

    def test_delete_keeps_shared_bound_asset(self):
        """绑定到本项目但归属其它项目的共享资产 → 仅解绑保留，不删除。"""
        from unittest.mock import MagicMock, patch
        from app.services.project_service import delete

        pid = uuid.uuid4()
        other_pid = uuid.uuid4()
        bound_id = uuid.uuid4()
        shared = MagicMock()
        shared.project_id = other_pid

        db = MagicMock()
        project = MagicMock()
        project.episodes = []
        project.tasks = []

        def _get(cls, pk):
            if cls is Project:
                return project
            if cls is Asset:
                return shared if pk == bound_id else None
            return None

        db.get.side_effect = _get
        # owned_ids=[]，bound_ids=[bound_id]；project_ids_of 查询返回 [other_pid]
        db.scalars.return_value.all.side_effect = [[], [bound_id], [other_pid], []]

        with patch("app.services.asset_service.delete_with_media") as mock_dm, \
             patch("os.path.isdir", return_value=False):
            result = delete(db, pid)

        assert result is True
        mock_dm.assert_not_called()  # 共享资产不删除
