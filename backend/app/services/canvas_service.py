"""画布(生图工作台)业务服务:文档 CRUD + 版本/回滚 + 分镜导入 + 批量生成派发。"""
import copy
import json
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetType
from app.models.canvas_board import CanvasBoard
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.canvas import CanvasBoardGenerate

logger = logging.getLogger(__name__)

_ASSET_KIND = {AssetType.character: "character", AssetType.scene: "scene", AssetType.prop: "prop"}


def list_boards(db: Session, project_id=None) -> list[CanvasBoard]:
    stmt = select(CanvasBoard).order_by(CanvasBoard.updated_at.desc())
    if project_id is not None:
        stmt = stmt.where(CanvasBoard.project_id == project_id)
    return list(db.scalars(stmt))


def get(db: Session, board_id) -> CanvasBoard:
    board = db.get(CanvasBoard, board_id)
    if board is None:
        raise ValueError("画布不存在")
    return board


def create(db: Session, name: str, project_id=None) -> CanvasBoard:
    board = CanvasBoard(name=name, project_id=project_id, document={"nodes": [], "edges": []}, version=1)
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


def save(db: Session, board_id, document: dict, name: str | None = None) -> CanvasBoard:
    board = get(db, board_id)
    board.snapshot = {"document": copy.deepcopy(board.document), "version": board.version}
    board.document = document or {"nodes": [], "edges": []}
    board.version = (board.version or 1) + 1
    if name:
        board.name = name
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


def delete(db: Session, board_id) -> None:
    board = get(db, board_id)
    db.delete(board)
    db.commit()


def rollback(db: Session, board_id) -> CanvasBoard:
    board = get(db, board_id)
    if not board.snapshot:
        raise ValueError("无可回滚的上一版本")
    prev_doc = board.snapshot.get("document")
    if prev_doc is None:
        raise ValueError("快照数据缺失")
    board.document = prev_doc
    board.version = max(1, board.snapshot.get("version") or (board.version - 1))
    board.snapshot = None
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


def _asset_preview_url(asset: Asset) -> str | None:
    if asset.character_sheet_url:
        return asset.character_sheet_url
    if asset.four_view_urls:
        return asset.four_view_urls[0]
    return asset.cover_url


def _resolve_asset(db: Session, ref) -> Asset | None:
    """character_ids / prop_ids / scene_id 引用可能是 UUID 字符串或名称,尽量解析。"""
    if not ref:
        return None
    try:
        return db.get(Asset, uuid.UUID(str(ref)))
    except (ValueError, TypeError):
        return None


def import_from_segment(db: Session, segment_id, name: str | None = None) -> CanvasBoard:
    """单分镜导入(兼容原行为):委托多分镜导入。"""
    return import_segments(db, [segment_id], name)


def import_segments(db: Session, segment_ids: list, name: str | None = None) -> CanvasBoard:
    """把一组真实分镜(及其关联资产)落成画布文档(M3 导演层·多分镜合并导入)。

    - 分镜按 (幕索引, 镜索引) 排序铺成网格;资产按唯一 id 去重放顶栏;
    - 同幕分镜之间生成 sequence 衔接边;资产 → 用到它的分镜生成 reference 边;
    - 每幕生成一个 group 分组节点框住所属分镜;
    - 分镜节点 data 带 segmentId/projectId/compositionPoint,画布内可直接跳剪辑器/标注回写。
    """
    segs: list[Segment] = []
    for sid in segment_ids:
        seg = db.get(Segment, sid)
        if seg is None:
            raise ValueError(f"分镜不存在:{sid}")
        segs.append(seg)
    segs.sort(key=lambda s: (s.episode.index, s.index))

    # 资产去重收集(跨分镜共享的资产只放一份)
    assets: dict[str, Asset] = {}
    for seg in segs:
        for rid in [seg.scene_id, *seg.character_ids, *seg.prop_ids]:
            a = _resolve_asset(db, rid)
            if a is not None:
                assets[str(a.id)] = a

    nodes: list[dict] = []
    edges: list[dict] = []
    ax = -520
    for aid, a in assets.items():
        url = _asset_preview_url(a)
        nodes.append({
            "id": f"asset-{aid}",
            "type": "asset",
            "position": {"x": ax, "y": -180},
            "data": {
                "label": a.name or "资产",
                "kind": _ASSET_KIND.get(a.type, "character"),
                "assetId": aid,
                "status": "idle",
                "progress": 0,
                "versions": [{"id": "v0", "url": url, "createdAt": "", "note": "导入快照"}] if url else [],
                "activeVersion": 0,
            },
        })
        ax += 280

    # 分镜节点铺网格 + 分组节点按幕框选
    per_row = 4
    shot_nodes: dict[str, dict] = {}
    shots_by_ep: dict[uuid.UUID, list[Segment]] = {}
    grid_i = 0
    for seg in segs:
        col = grid_i % per_row
        row = grid_i // per_row
        nid = str(seg.id)
        shot_nodes[nid] = {
            "id": nid,
            "type": "shot",
            "position": {"x": 60 + col * 300, "y": 100 + row * 260},
            "data": {
                "label": f"第 {seg.index} 镜",
                "description": seg.description or "",
                "prompt": seg.description or "",
                "segmentId": nid,
                "projectId": str(seg.episode.project_id) if seg.episode else None,
                "shotType": seg.shot_type,
                "camera": seg.camera,
                "emotion": seg.emotion,
                "durationSec": seg.duration,
                "ratio": "16:9",
                "compositionPoint": seg.composition_point,
                "status": "idle",
                "progress": 0,
                "versions": [],
                "activeVersion": 0,
            },
        }
        grid_i += 1
        shots_by_ep.setdefault(seg.episode_id, []).append(seg)
    # 幕分组节点(装饰性框 + 幕标题)
    grid_w = per_row * 300
    for ep_id, slist in shots_by_ep.items():
        ep = slist[0].episode
        xs = [shot_nodes[str(s.id)]["position"]["x"] for s in slist]
        ys = [shot_nodes[str(s.id)]["position"]["y"] for s in slist]
        top = min(ys) - 70
        bottom = max(ys) + 240
        left = min(xs) - 30
        right = max(xs) + 270
        nodes.append({
            "id": f"group-ep-{str(ep_id)[:8]}",
            "type": "group",
            "position": {"x": left, "y": top},
            "data": {
                "label": f"第 {ep.index} 幕 · {ep.title or '导入幕'}" if ep else "导入幕",
                "status": "idle",
                "memberCount": len(slist),
                "w": right - left,
                "h": bottom - top,
            },
            "width": right - left,
            "height": bottom - top,
        })
    nodes += list(shot_nodes.values())

    # 衔接边(same-episode 相邻) + 参考边(资产 → 用到它的分镜)
    for i in range(len(segs) - 1):
        a_seg, b_seg = segs[i], segs[i + 1]
        if a_seg.episode_id == b_seg.episode_id:
            edges.append({
                "id": f"e-seq-{str(a_seg.id)[:8]}-{str(b_seg.id)[:8]}",
                "source": str(a_seg.id),
                "target": str(b_seg.id),
                "type": "canvas",
                "data": {"semantic": "sequence", "label": "衔接"},
            })
    for seg in segs:
        for rid in [seg.scene_id, *seg.character_ids, *seg.prop_ids]:
            a = _resolve_asset(db, rid)
            if a is not None:
                edges.append({
                    "id": f"e-ref-{str(seg.id)[:8]}-{str(a.id)[:8]}",
                    "source": f"asset-{a.id}",
                    "target": str(seg.id),
                    "type": "canvas",
                    "data": {"semantic": "reference", "label": "参考"},
                })

    ep0 = segs[0].episode if segs else None
    doc = {"nodes": nodes, "edges": edges}
    board = CanvasBoard(
        name=name or f"导入 {len(segs)} 个分镜",
        project_id=ep0.project_id if ep0 else None,
        document=doc,
        version=1,
    )
    db.add(board)
    db.commit()
    db.refresh(board)
    return board


def audit_board(db: Session, board_id) -> dict:
    """画布体检/评审(替代 P5 camera_plan 数据面,纯读):汇总节点级问题与任务运行状态。"""
    board = get(db, board_id)
    doc = board.document or {}
    items: list[dict] = []
    for n in doc.get("nodes", []):
        nid = n.get("id", "")
        data = n.get("data") or {}
        label = data.get("label") or nid
        ntype = n.get("type")
        if ntype in ("shot", "asset") and not data.get("segmentId") and not data.get("assetId"):
            items.append({"node_id": nid, "label": label, "issue": "unbound", "detail": "未绑定分镜/资产,生成走本地演示"})
            continue
        if ntype == "shot":
            if data.get("status") == "failed" or data.get("videoError"):
                items.append({
                    "node_id": nid, "label": label, "issue": "failed",
                    "detail": str(data.get("error") or data.get("videoError") or "")[:120],
                })
            busy = data.get("status") in ("running", "queued")
            if not busy and not data.get("videoUrl") and not (data.get("versions") or []):
                items.append({
                    "node_id": nid, "label": label, "issue": "no_product",
                    "detail": "关键帧与视频均未生成",
                })
            if not data.get("compositionPoint"):
                items.append({
                    "node_id": nid, "label": label, "issue": "no_point",
                    "detail": "未标注九宫格构图点位",
                })
    running_tasks = db.scalar(
        select(func.count(Task.id)).where(
            Task.type == TaskType.canvas_generate,
            Task.target_type == "canvas_board",
            Task.target_id == board_id,
            Task.status.in_([TaskStatus.pending, TaskStatus.running]),
        )
    ) or 0
    failed_rows = list(
        db.scalars(
            select(Task)
            .where(
                Task.type == TaskType.canvas_generate,
                Task.target_type == "canvas_board",
                Task.target_id == board_id,
                Task.status == TaskStatus.failed,
            )
            .order_by(Task.updated_at.desc())
            .limit(3)
        )
    )
    return {
        "board_id": str(board_id),
        "items": items,
        "running_tasks": running_tasks,
        "failed_tasks": len(failed_rows),
        "recent_failed": [
            {"task_id": str(t.id), "error": (t.error or "")[:200]} for t in failed_rows
        ],
    }


def generate(db: Session, board_id, payload: CanvasBoardGenerate):
    """画布批量生成:校验每个节点绑定的分镜后,创建 canvas_generate 父任务并派发。

    未绑定分镜/分镜不存在的节点立即在响应中带 error(不派发);
    全部无效则抛 ValueError(HTTP 400)。
    """
    board = get(db, board_id)
    doc = board.document or {}
    nodes_by_id = {n["id"]: n for n in doc.get("nodes", [])}

    cfg_nodes: list[dict] = []
    results: list[dict] = []
    for node_id in payload.node_ids:
        node = nodes_by_id.get(node_id)
        if node is None:
            results.append({"node_id": node_id, "error": "节点不存在"})
            continue
        data = node.get("data") or {}
        seg_ref = data.get("segmentId")
        if not seg_ref:
            results.append({"node_id": node_id, "error": "节点未绑定分镜(请先从分镜导入)"})
            continue
        try:
            seg_uuid = uuid.UUID(seg_ref)
        except (ValueError, TypeError):
            results.append({
                "node_id": node_id,
                "error": f"分镜 「{seg_ref}」 不是有效 ID(节点可能来自手动拖拽,请删除后用「从分镜导入」重新放入真实分镜)",
            })
            continue
        seg = db.get(Segment, seg_uuid)
        if seg is None:
            results.append({"node_id": node_id, "error": f"分镜 {seg_ref} 不存在"})
            continue
        cfg_nodes.append({
            "node_id": node_id,
            "segment_id": str(seg.id),
            "model_id": str(payload.model_id) if payload.model_id else None,
            "ratio": payload.ratio,
            "prompt": data.get("prompt") or "",
            "kind": payload.kind or "image",
        })

    if not cfg_nodes:
        first_err = results[0].get("error", "节点均未绑定有效分镜") if results else "节点均未绑定有效分镜"
        raise ValueError(f"没有可生成的节点:{first_err}")

    task = Task(
        project_id=board.project_id,
        type=TaskType.canvas_generate,
        target_type="canvas_board",
        target_id=board.id,
        status=TaskStatus.pending,
        provider_task_id=json.dumps({"board_id": str(board.id), "nodes": cfg_nodes}),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from app.tasks.canvas_generate import canvas_generate
    canvas_generate.delay(str(task.id))

    for c in cfg_nodes:
        results.append({"node_id": c["node_id"]})
    return task, results
