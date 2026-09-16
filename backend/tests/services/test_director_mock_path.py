"""导演台 mock 链路与画布回写单测（2026-08-29，无服务器依赖）。"""
import json
import uuid

from app.config import settings
from app.models.canvas_board import CanvasBoard
from app.tasks.canvas_director_generate import _make_mock_video, _patch_director_board


def _board(document: dict | None = None) -> CanvasBoard:
    return CanvasBoard(
        id=uuid.uuid4(),
        name="测试画布",
        project_id=None,
        document=document or {"nodes": [], "edges": []},
        version=1,
    )


def _dummy_db(board):
    """无 DB 会话：add/commit 落内存对象即可。"""
    import types

    return types.SimpleNamespace(add=lambda x: setattr(x, "_added", True), commit=lambda: None, get=lambda cls, pid: board)


def test_make_mock_video(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    url = _make_mock_video("task-123", (832, 480))
    assert url.endswith("/static/media/director/task-123/full.mp4")
    p = tmp_path / "director" / "task-123" / "full.mp4"
    assert p.exists() and p.stat().st_size > 0


def test_make_mock_video_no_ffmpeg_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", str(tmp_path))
    from app.tasks import canvas_director_generate as mod

    orig = mod.shutil.which
    mod.shutil.which = lambda *a, **k: None
    try:
        url = _make_mock_video("task-2", (16, 16))
    finally:
        mod.shutil.which = orig
    assert url.endswith("/full.mp4")
    p = tmp_path / "director" / "task-2" / "full.mp4"
    assert p.exists()


def test_patch_director_board_adds_video_node():
    doc = {
        "nodes": [
            {"id": "n1", "type": "shot", "position": {"x": 100, "y": 80},
             "data": {"label": "镜1", "segmentId": "11111111-1111-1111-1111-111111111111", "status": "queued"}},
            {"id": "n2", "type": "shot", "position": {"x": 400, "y": 80},
             "data": {"label": "镜2", "segmentId": "22222222-2222-2222-2222-222222222222", "status": "queued"}},
        ],
        "edges": [],
    }
    board = _board(doc)
    db = _dummy_db(board)
    cfg = {
        "board_id": str(board.id),
        "nodes": [{"node_id": "n1"}, {"node_id": "n2"}],
        "_task_id": "task-x",
        "total_frames": 248,
        "frame_rate": 24,
    }
    _patch_director_board(db, board, cfg, "http://x/full.mp4", "mock 占位")

    nodes = board.document["nodes"]
    assert len(nodes) == 3
    shot = next(n for n in nodes if n["id"] == "n1")
    assert shot["data"]["status"] == "done"
    assert shot["data"]["directorTaskId"] == "task-x"
    video = next(n for n in nodes if n["type"] == "video")
    assert video["data"]["videoUrl"] == "http://x/full.mp4"
    assert abs(video["data"]["durationSec"] - 248 / 24) < 0.01
    assert video["data"]["kind"] == "director"
    # shot 节点不被整片 URL 覆盖
    assert "videoUrl" not in shot["data"] or shot["data"].get("videoUrl") is None


def test_patch_director_board_preserves_existing_video_node():
    doc = {
        "nodes": [
            {"id": "n1", "type": "shot", "position": {"x": 0, "y": 0}, "data": {"label": "镜1"}},
            {"id": "__VID__", "type": "video",
             "data": {"label": "旧整片", "videoUrl": "http://old", "status": "done"}},
        ],
        "edges": [],
    }
    board = _board(doc)
    # 预设节点 id 改写为确定性 id（模拟”同画布重跑“）——_patch 只会复用 id 匹配的节点
    doc["nodes"][1]["id"] = f"video-director-{str(board.id)[:8]}"
    db = _dummy_db(board)
    cfg = {"board_id": str(board.id), "nodes": [{"node_id": "n1"}], "_task_id": "t2",
           "total_frames": 107, "frame_rate": 24}
    _patch_director_board(db, board, cfg, "http://new/full.mp4", "更新")
    ids = [n["id"] for n in board.document["nodes"]]
    assert len(ids) == 2  # 不重复新增
    v = next(n for n in board.document["nodes"] if n["type"] == "video")
    assert v["data"]["videoUrl"] == "http://new/full.mp4"
    assert v["data"]["label"] == "旧整片"  # 更新而非重建
