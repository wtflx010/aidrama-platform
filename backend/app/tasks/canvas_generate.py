"""画布批量生成编排:逐节点按 kind 分流(关键帧 image / 镜头视频 video),
轮询子任务终态后把产物(版本图 URL / 视频节点 / 错误)回写 canvas_board.document。
"""
import copy
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.canvas_board import CanvasBoard
from app.models.media import Keyframe, VideoClip
from app.models.task import Task, TaskStatus
from app.schemas.keyframe import KeyframeGenerate
from app.schemas.video import VideoGenerate
from app.services import keyframe_service, video_service
from app.tasks.base import now, update_task
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

POLL_INTERVAL = 3  # 秒
TIMEOUT = 1800  # 30 分钟


def _patch_board(db, board: CanvasBoard, updates: dict[str, dict]):
    """把生成结果回写画布文档(深拷贝防 JSONB 等值不 flush):

    - image 成功:节点追加版本图(versions+activeVersion)
    - image 失败:节点标记 failed + error
    - video 成功:镜头节点记 videoUrl/videoTaskId,并在其右下方派生/更新视频节点
    - video 失败:镜头节点记 videoError(不覆盖关键帧相关状态)

    不 bump version(任务回写不污染手工保存的历史版本)。
    """
    doc = copy.deepcopy(board.document or {})
    nodes = doc.get("nodes") or []
    changed = False
    for n in nodes:
        nid = n.get("id")
        u = updates.get(nid)
        if not u:
            continue
        data = dict(n.get("data") or {})
        kind = u.get("kind", "image")
        if u.get("url"):
            if kind == "video":
                vid = u.get("videoNodeId")
                vdata = {
                    "label": f"{data.get('label') or '镜头'} · 视频",
                    "description": "画布 R2V 生成",
                    "durationSec": u.get("durationSec") or 5,
                    "videoUrl": u["url"],
                    "videoTaskId": u.get("taskId"),
                    "status": "done",
                    "sourceNodeId": nid,  # 2026-08-30:用于清理同源旧视频节点
                }
                vnode = next((x for x in nodes if x.get("id") == vid), None)
                if vnode is not None:
                    vnode["data"] = vdata
                else:
                    # 同源视频节点只保留最新一条（2026-08-30 修复:重复提交不堆积死节点）
                    nodes[:] = [
                        x for x in nodes
                        if not (x.get("type") == "video" and (x.get("data") or {}).get("sourceNodeId") == nid)
                    ]
                    pos = n.get("position") or {"x": 0, "y": 0}
                    nodes.append({
                        "id": vid,
                        "type": "video",
                        "position": {"x": pos.get("x", 0) + 440, "y": pos.get("y", 0) + 160},
                        "data": vdata,
                    })
                data["videoUrl"] = u["url"]
                data["videoTaskId"] = u.get("taskId")
                data.pop("videoError", None)
            else:
                versions = list(data.get("versions") or [])
                versions.append({
                    "id": f"v-{uuid.uuid4()}",
                    "url": u["url"],
                    "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "note": "画布生成",
                })
                data["versions"] = versions
                data["activeVersion"] = len(versions) - 1
                data["status"] = "done"
                data["progress"] = 100
                data.pop("error", None)
        elif u.get("error"):
            if kind == "video":
                data["videoError"] = u["error"]
            else:
                data["status"] = "failed"
                data["progress"] = 100
                data["error"] = u["error"]
        n["data"] = data
        changed = True
    if changed:
        board.document = doc
        db.add(board)
        db.commit()


@celery_app.task(name="canvas_generate", bind=True)
def canvas_generate(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return  # 任务被级联删除
        try:
            cfg = json.loads(task.provider_task_id or "{}")
        except json.JSONDecodeError:
            update_task(db, task_id, status=TaskStatus.failed, error="配置解析失败", finished_at=now())
            return
        board = db.get(CanvasBoard, cfg.get("board_id"))
        if board is None:
            update_task(db, task_id, status=TaskStatus.failed, error="画布不存在", finished_at=now())
            return
        nodes_cfg = cfg.get("nodes") or []
        if not nodes_cfg:
            update_task(db, task_id, status=TaskStatus.failed, error="无生成节点", finished_at=now())
            return

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=1)

        # 派发子任务:node_id -> {task_id, kind};派发失败记录节点级错误
        subs: dict[str, dict] = {}
        dispatch_errors: dict[str, dict] = {}
        for nc in nodes_cfg:
            if not nc.get("segment_id"):
                dispatch_errors[nc["node_id"]] = {"error": "节点未绑定分镜", "kind": nc.get("kind", "image")}
                continue
            kind = nc.get("kind") or "image"
            try:
                if kind == "video":
                    kwargs: dict = {}
                    if nc.get("model_id"):
                        kwargs["model_id"] = nc["model_id"]
                    if nc.get("prompt"):
                        kwargs["prompt"] = nc["prompt"]
                    _, sub_task = video_service.generate(db, nc["segment_id"], VideoGenerate(**kwargs))
                else:
                    payload = KeyframeGenerate(
                        prompt=nc.get("prompt") or "",
                        model_id=nc.get("model_id"),
                        ratio=nc.get("ratio"),
                    )
                    _, sub_task = keyframe_service.generate(db, nc["segment_id"], payload)
                subs[nc["node_id"]] = {"task_id": str(sub_task.id), "kind": kind}
            except Exception as exc:  # noqa: BLE001 - 节点级失败不阻断其它节点
                dispatch_errors[nc["node_id"]] = {"error": str(exc), "kind": kind}
                logger.exception("画布节点派发失败 node_id=%s kind=%s", nc["node_id"], kind)

        if not subs:
            update_task(db, task_id, status=TaskStatus.failed, error="全部节点生成失败", finished_at=now())
            _patch_board(db, board, {nid: v for nid, v in dispatch_errors.items()})
            return

        # 轮询子任务
        start = time.time()
        merged: dict[str, dict] = {}
        while time.time() - start < TIMEOUT:
            db.expire_all()
            cur = db.get(Task, task_id)
            if cur is None or cur.status == TaskStatus.cancelled:
                # 2026-08-30:取消/删除前 flush 已得子结果，避免节点永久 running
                if merged:
                    _patch_board(db, board, merged)
                return  # 取消保护:不覆盖 cancelled
            done = succ = 0
            updates: dict[str, dict] = {}
            for node_id, info in subs.items():
                kind = info["kind"]
                sub = db.get(Task, info["task_id"])
                if sub is None:
                    updates[node_id] = {"error": "子任务丢失", "kind": kind}
                    done += 1
                    continue
                st = sub.status
                if st == TaskStatus.succeeded:
                    if kind == "video":
                        clip = db.get(VideoClip, sub.target_id)
                        if clip and clip.video_url:
                            updates[node_id] = {
                                "url": clip.video_url,
                                "kind": kind,
                                "taskId": info["task_id"],
                                "videoNodeId": f"video-{info['task_id'][:8]}",
                                "durationSec": int((clip.num_frames or 121) / max(clip.frame_rate or 24, 1)),
                            }
                        else:
                            updates[node_id] = {"error": "视频产物缺失", "kind": kind}
                    else:
                        kf = db.get(Keyframe, sub.target_id)
                        updates[node_id] = (
                            {"url": kf.image_url, "kind": kind} if kf and kf.image_url
                            else {"error": "关键帧无产物", "kind": kind}
                        )
                    succ += 1
                    done += 1
                elif st in (TaskStatus.failed, TaskStatus.cancelled):
                    updates[node_id] = {
                        "error": sub.error or ("已取消" if st == TaskStatus.cancelled else "生成失败"),
                        "kind": kind,
                    }
                    done += 1
            merged = dict(updates)
            for nid, v in dispatch_errors.items():
                merged.setdefault(nid, v)
            total = len(subs) + len(dispatch_errors)
            progress = min(99, 1 + int(done / max(total, 1) * 98))
            update_task(db, task_id, progress=progress)
            if done >= len(subs):
                _patch_board(db, board, merged)
                failed_n = total - succ
                update_task(
                    db, task_id,
                    status=TaskStatus.succeeded if failed_n == 0 else TaskStatus.failed,
                    progress=100,
                    finished_at=now(),
                    error=None if failed_n == 0 else f"成功 {succ}/{total},失败 {failed_n}",
                )
                return
            time.sleep(POLL_INTERVAL)

        if merged:
            _patch_board(db, board, merged)  # 2026-08-30:超时也 flush 已得子结果
        update_task(db, task_id, status=TaskStatus.failed, error="画布批量生成超时", finished_at=now())
    finally:
        db.close()
