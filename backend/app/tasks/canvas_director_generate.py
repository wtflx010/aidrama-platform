"""画布导演台模式任务（2026-08-29）：真实提交 166 或 mock 离线模拟。

真实链路：ComfyUIProvider.director_generate（探测导演台节点 → 上传参考图 → 提交工作流）→
run_with_polling → 下载整片 → 回写画布 document。
mock 链路（DIRECTOR_MODE=mock，166 关机时的开发通道）：本地复制/生成占位 mp4，
全链路（任务/轮询/回写）与真实一致，仅不走 166。"""
import copy
import json
import logging
import os
import random
import shutil
import time

from app.config import settings
from app.database import SessionLocal
from app.models.canvas_board import CanvasBoard
from app.models.task import Task, TaskStatus
from app.tasks.base import TaskCancelledError, download_to_local, now, run_with_polling, update_task
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _make_mock_video(task_id: str, box: tuple[int, int]) -> str:
    """mock 模式产物：优先复用已有 mp4，无则 ffmpeg 灰底测试片，再退化为占位文件。"""
    w, h = int(box[0] or 832), int(box[1] or 480)
    subdir = f"director/{task_id}"
    local_dir = os.path.join(settings.media_dir, subdir)
    os.makedirs(local_dir, exist_ok=True)
    path = os.path.join(local_dir, "full.mp4")
    src: str | None = None
    base = os.path.join(settings.media_dir, "videos")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            for name in files:
                if name.endswith(".mp4"):
                    src = os.path.join(root, name)
                    break
            if src:
                break
    if src and os.path.getsize(src) > 0:
        shutil.copyfile(src, path)
    else:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            import subprocess as sp

            args = [ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}:d=5",
                    "-f", "lavfi", "-i", "anullsrc=r=32000:cl=stereo", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path]
            try:
                sp.run(args, capture_output=True, timeout=90, check=True)
            except Exception:
                pass
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        with open(path, "wb") as fh:
            fh.write(b"mock-director-placeholder")
    return f"{settings.static_base_url}/media/{subdir}/full.mp4"


def _patch_director_board(db, board: CanvasBoard, cfg: dict, video_url: str, note: str) -> None:
    """回写画布 document：新增整片 video 节点 + 各 shot 节点终态标记。"""
    doc = copy.deepcopy(board.document or {})
    nodes = doc.get("nodes") or []
    node_ids = [n["node_id"] for n in cfg.get("nodes") or []]
    vid = f"video-director-{str(cfg.get('board_id'))[:8]}"
    first_shot = next((n for n in nodes if n.get("id") in node_ids), None)
    pos = (first_shot.get("position") or {"x": 0, "y": 0}) if first_shot else {"x": 0, "y": 0}
    curated: list[dict] = []
    for n in nodes:
        data = dict(n.get("data") or {})
        if n.get("id") in node_ids:
            data["status"] = "done"
            data["progress"] = 100
            data["directorTaskId"] = str(cfg.get("_task_id") or "")
            data.pop("videoError", None)
            curated.append({**n, "data": data})
        elif n.get("id") == vid:
            data["videoUrl"] = video_url
            data["description"] = note
            data["status"] = "done"
            data.pop("videoError", None)
            curated.append({**n, "data": data})
        else:
            curated.append(n)
    if not any(n.get("id") == vid for n in curated):
        frames = int(cfg.get("total_frames") or 0)
        fps = float(cfg.get("frame_rate") or 24)
        curated.append({
            "id": vid, "type": "video",
            "position": {"x": int(pos.get("x", 0)) + 440, "y": int(pos.get("y", 0)) + 160},
            "data": {
                "label": f"多段连续片 · {len(node_ids)} 镜",
                "description": note or "画布导演台整片产物",
                "videoUrl": video_url,
                "videoTaskId": str(cfg.get("_task_id") or ""),
                "durationSec": round(frames / fps, 2) if frames else 5,
                "status": "done", "kind": "director",
            },
        })
    # 同步产物到方案节点自身（ComfyUI 式节点内预览）
    _scheme_node_id = cfg.get("scheme_node_id")
    if _scheme_node_id:
        for _n in curated:
            if _n.get("id") == _scheme_node_id:
                _d = dict(_n.get("data") or {})
                _d.update({
                    "status": "done", "progress": 100,
                    "videoUrl": video_url, "previewUrl": video_url,
                    "videoTaskId": str(cfg.get("_task_id") or ""),
                    "directorTaskId": str(cfg.get("_task_id") or ""),
                    "error": None, "videoError": None,
                })
                _n["data"] = _d
                break
    board.document = {"nodes": curated, "edges": doc.get("edges") or []}
    db.add(board)
    db.commit()


def _mark_nodes_failed(board_id: str | None, node_ids: list[str], scheme_node_id=None) -> None:
    """失败时回写画布：参与节点与方案节点标记 failed（独立 Session，避免复用失效事务）。"""
    if not board_id or not node_ids:
        return
    try:
        db2 = SessionLocal()
        board = db2.get(CanvasBoard, board_id)
        if board is None:
            db2.close()
            return
        doc = copy.deepcopy(board.document or {})
        curated: list[dict] = []
        for n in (doc.get("nodes") or []):
            data = dict(n.get("data") or {})
            if n.get("id") in node_ids or n.get("id") == scheme_node_id:
                data["status"] = "failed"
                data["videoError"] = "导演台任务失败，请查看任务中心详情"
            curated.append({**n, "data": data})
        board.document = {"nodes": curated, "edges": doc.get("edges") or []}
        db2.add(board)
        db2.commit()
        db2.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("导演台失败回写节点失败: %s", exc)


@celery_app.task(name="canvas_director_generate", bind=True)
def canvas_director_generate(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        cfg = json.loads(task.provider_task_id or "{}")
        board = db.get(CanvasBoard, cfg.get("board_id"))
        if board is None:
            raise ValueError("画布不存在")
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=3)

        mode = str(settings.director_mode or "mock").strip().lower()
        total_frames = int(cfg.get("total_frames") or 0)
        fps = float(cfg.get("frame_rate") or 24)
        local_url = ""
        note = ""

        if mode == "real":
            from app.models.model_config import Model
            from app.providers.registry import ProviderRegistry

            model = db.get(Model, cfg.get("model_id")) if cfg.get("model_id") else None
            if model is None or not model.is_enabled:
                raise ValueError("导演台模型不存在或已停用")
            provider = ProviderRegistry.for_model(model, resolution=str(cfg.get("res") or "") or None)
            if not provider.director_node_available():
                raise ValueError(
                    "166 ComfyUI 未安装 MiniMax H3 Director 插件（导演台模式不可用）。"
                    "请先在 166 安装 AIMixer/ComfyUI_MiniMaxH3_Director，"
                    "或配置 DIRECTOR_MODE=mock 在离线态验证"
                )
            handle = provider.director_generate(
                cfg.get("timeline") or "",
                task_type=cfg.get("task_type") or "r2v — 参考主体生视频(Reference to Video)",
                global_prompt=cfg.get("global_prompt") or "",
                width=int(cfg.get("width") or 832), height=int(cfg.get("height") or 480),
                ref_max_size=int(cfg.get("ref_max_size") or 864),
                total_frames=total_frames, frame_rate=fps,
                steps=cfg.get("steps"), sampler=cfg.get("sampler") or "res_multistep",
                scheduler=cfg.get("scheduler") or "simple", cfg=cfg.get("cfg"), seed=cfg.get("seed"),
                shift_video=cfg.get("shift_video"), shift_audio=cfg.get("shift_audio"),
            )
            cfg["prompt_id"] = handle.providerTaskId
            cfg_saved = json.loads((db.get(Task, task_id).provider_task_id) or "{}") if db.get(Task, task_id) else {}
            cfg_saved["prompt_id"] = handle.providerTaskId
            t = db.get(Task, task_id)
            if t is not None:
                t.provider_task_id = json.dumps(cfg_saved, ensure_ascii=False)
                t.poll_url = handle.pollUrl
                t.provider = handle.provider
                db.commit()
            result = run_with_polling(
                db, task_id, provider, handle,
                poll_interval=settings.celery_video_poll_interval,
                timeout=settings.celery_video_timeout,
            )
            if not result.videoUrl:
                raise ValueError("导演台任务完成但未返回视频 URL")
            local_url = download_to_local(
                result.videoUrl, subdir=f"director/{task_id}", filename="full.mp4", task_id=str(task_id),
            )
            note = "166 真实生成"
        else:
            for i in range(4):
                time.sleep(1.0)
                update_task(db, task_id, status=TaskStatus.running, progress=10 + i * 18 + random.randint(0, 8))
            time.sleep(0.4)
            update_task(db, task_id, status=TaskStatus.running, progress=90)
            local_url = _make_mock_video(str(task_id), (int(cfg.get("width") or 832), int(cfg.get("height") or 480)))
            note = "mock 占位（166 离线）；切换 DIRECTOR_MODE=real 后走真实 166"

        cfg["_task_id"] = str(task_id)
        try:
            _patch_director_board(db, board, cfg, local_url, note)
        except Exception as exc:  # noqa: BLE001 回写失败不吞掉产物
            logger.warning("导演台 doc 回写失败(产物已生成): %s", exc)
        update_task(db, task_id, status=TaskStatus.succeeded, progress=100, result_url=local_url, finished_at=now())
        db.commit()
    except TaskCancelledError:
        db.rollback()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        from app.providers.errors import map_to_chinese

        msg = map_to_chinese(exc)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        try:
            t2 = db.get(Task, task_id)
            cfg = json.loads((t2.provider_task_id or "{}") if t2 else "{}")
            _mark_nodes_failed(
                cfg.get("board_id"),
                [n["node_id"] for n in cfg.get("nodes") or []] if cfg.get("nodes") else [],
                cfg.get("scheme_node_id"),
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
