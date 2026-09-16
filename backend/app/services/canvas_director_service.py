"""画布导演台模式业务服务（2026-08-29）：多段连续生视频。"""
import json

from sqlalchemy.orm import Session

from app.constants import snap_h3_frames
from app.models.canvas_board import CanvasBoard
from app.models.model_config import Model
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.providers.comfyui import _MMAX_RES_GRADES  # 分辨率档位（与 provider 一致）
from app.services.video_pipeline.model import resolve_director_model
from app.services.video_pipeline.refs import collect_global_refs

logger = __import__("logging").getLogger(__name__)


MAX_DIRECTOR_FRAMES = 362  # 单段帧数上限（与单镜链路 frames_max 对齐，防时长越界/爆显存）
MAX_DIRECTOR_DURATION_SEC = 15.0


def _snap_h3_frames(frames: int) -> int:
    """MiniMax H3 17n+5 帧数网格（与 provider frames 对齐），带上限。

    2026-09-17 复用共享 snap_h3_frames，与单镜链路保持一致（消除两套网格实现）。
    """
    return snap_h3_frames(frames)


def _segment_duration_sec(segment: Segment, default: float = 5.0) -> float:
    d = getattr(segment, "duration", None)
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = None
    return d if d and d > 0 else default


def _resolve_dims(ratio: str | None, res: str | None) -> tuple[int, int, int]:
    """画布 ratio/res → 导演台画布宽高（32 倍数档位）。返回 (width, height, ref_max_size)。"""
    grade_map = _MMAX_RES_GRADES.get(str(res or "768p").strip(), _MMAX_RES_GRADES["768p"])
    r = str(ratio or "16:9").replace("：", ":")
    w, h = grade_map.get(r, grade_map["16:9"])
    return w, h, (w if w >= h else h)


def _resolve_director_model(db: Session, model_id=None) -> Model | None:
    """导演台视频模型：显式 model_id 优先；否则第一个启用的 MiniMax ComfyUI 视频模型。

    2026-09 收敛：逻辑点在 video_pipeline.model.resolve_director_model，此处仅转发。"""
    return resolve_director_model(db, model_id)


def _collect_global_refs(db: Session, segments: list[Segment]) -> list[dict]:
    """跨分镜去重收集公共参考图（角色/场景/道具），上限 9 张。

    2026-09 收敛：逻辑点在 video_pipeline.refs.collect_global_refs，此处仅转发。"""
    return collect_global_refs(db, segments)


def build_director_timeline(
    segments,
    *,
    global_refs: list[dict],
    global_prompt: str,
    task_type_value: str,
    fps: float,
    width: int,
    height: int,
    ref_max_size: int,
    context_enabled: bool,
    context_frames: int,
    audio_mode: str = "generate",
) -> str:
    """组装导演台时间轴 JSON（editMode=segment，r2v 公共参数 + 逐段）。
    segments 元素为 (segment, duration_sec, prompt, from_prev) 元组。"""
    tl_segments: list[dict] = []
    start = 0
    for i, (seg, duration_sec, prompt, from_prev) in enumerate(segments):
        _ = seg  # 元组保留 segment 便于后续扩展（本轮 prompt/时长已展开）
        if i == 0:
            from_prev = False
        length = _snap_h3_frames(int(round(float(duration_sec) * float(fps))))
        tl_segments.append({
            "id": f"d{i}", "start": start, "length": length,
            "prompt": prompt or "", "taskType": task_type_value,
            "refs": [], "fromPrev": bool(from_prev),
        })
        start += length
    tl = {
        "version": 4, "editMode": "segment", "totalFrames": start,
        "frameRate": fps, "width": width, "height": height, "refMaxSize": ref_max_size,
        "output": {
            "mode": "fixed", "longEdge": ref_max_size, "width": width, "height": height,
            "maxExportFrames": 0, "exportMode": "all", "audioMode": audio_mode, "refImageSize": "match",
            "continuityEnabled": bool(context_enabled),
            "continuityOverlapFrames": (int(context_frames or 22) if context_enabled else 0),
        },
        "videoClips": [],
        "video": {"fileName": "", "videoFile": "", "subfolder": "", "type": "input", "frames": [], "frameMap": []},
        "global": {
            "taskType": task_type_value, "prompt": global_prompt or "",
            "refs": global_refs, "refAudios": [], "referenceVideo": {},
            "continuousReference": bool(context_enabled), "commonEnabled": True,
        },
        "segments": tl_segments,
    }
    return json.dumps(tl, ensure_ascii=False)


def generate(db: Session, board_id, payload):
    """导演台模式入口：校验 → 组装 timeline → 建任务派发。返回 (task, node_ids, total_frames)。"""
    board = db.get(CanvasBoard, board_id)
    if board is None:
        raise ValueError("画布不存在")
    doc = board.document or {}
    nodes_by_id = {n["id"]: n for n in doc.get("nodes", [])}
    if not payload.node_ids:
        raise ValueError("导演台模式至少需要一个分镜节点")

    cfg = payload.config or {}
    context_enabled = bool(cfg.get("context_enabled", True))
    try:
        context_frames = int(cfg.get("context_frames") or 22)
    except (TypeError, ValueError):
        context_frames = 22
    res = str(cfg.get("res") or "").strip() or "768p"
    ratio = str(cfg.get("ratio") or "").strip() or "16:9"
    task_type_value = str(cfg.get("task_type") or "").strip() or "r2v — 参考主体生视频(Reference to Video)"
    try:
        fps = float(cfg.get("fps") or 24.0)
    except (TypeError, ValueError):
        fps = 24.0
    width, height, ref_max_size = _resolve_dims(ratio, res)

    ordered: list[dict] = []
    if payload.shots:
        for st in payload.shots:
            node = nodes_by_id.get(st.node_id)
            data = (node.get("data") or {}) if node else {}
            sid = data.get("segmentId")
            seg = db.get(Segment, sid) if sid else None
            if seg is None:
                continue
            is_first = len(ordered) == 0
            ordered.append({
                "node_id": st.node_id, "segment": seg,
                "prompt": (st.prompt or "").strip() or (seg.description or ""),
                "duration_sec": min(float(st.duration_sec) if (st.duration_sec or 0) > 0 else _segment_duration_sec(seg), MAX_DIRECTOR_DURATION_SEC),
                "from_prev": False if is_first else bool(st.from_prev),
            })
    else:
        for nid in payload.node_ids:
            node = nodes_by_id.get(nid)
            if node is None:
                continue
            data = node.get("data") or {}
            sid = data.get("segmentId")
            seg = db.get(Segment, sid) if sid else None
            if seg is None:
                continue
            ordered.append({
                "node_id": nid, "segment": seg,
                "prompt": (data.get("prompt") or "").strip() or (seg.description or ""),
                "duration_sec": _segment_duration_sec(seg),
                "from_prev": len(ordered) != 0,
            })
    if not ordered:
        raise ValueError("所选节点均未绑定真实分镜；请先从分镜导入再使用导演台模式")

    for _o in ordered:
        _o["duration_sec"] = min(float(_o["duration_sec"] or 5.0), MAX_DIRECTOR_DURATION_SEC)
    rows_for_tl = [(o["segment"], o["duration_sec"], o["prompt"], o["from_prev"]) for o in ordered]
    # enable_common_refs=False：纯文生组合（t2v）不收集公共参考图（2026-08-29）
    enable_refs = bool(cfg.get("enable_common_refs", True))
    collect_refs = _collect_global_refs(db, [o["segment"] for o in ordered]) if enable_refs else []
    # r2v 但无任何参考图 → 自动降级 t2v 纯文生（2026-08-30 修复：避免空 refs 提交 166 行为不可控）
    is_r2v = ("r2v" in task_type_value.lower()) or ("reference to video" in task_type_value.lower())
    # r2v 但无任何参考图 → 自动降级 t2v 纯文生（避免空 refs 提交 166 行为不可控）
    if is_r2v and not collect_refs:
        task_type_value = "t2v — 文生视频(Text to Video)"
    timeline_json = build_director_timeline(
        rows_for_tl,
        global_refs=collect_refs,
        global_prompt=str(cfg.get("global_prompt") or "").strip(),
        task_type_value=task_type_value,
        fps=fps, width=width, height=height, ref_max_size=ref_max_size,
        context_enabled=context_enabled, context_frames=context_frames,
    )

    model = _resolve_director_model(db, cfg.get("model_id"))
    if model is None:
        raise ValueError("未找到可用的 MiniMax H3 ComfyUI 视频模型（导演台模式必需）")

    try:
        total_frames = sum(int(x["length"]) for x in json.loads(timeline_json).get("segments", []))
    except Exception:  # noqa: BLE001
        total_frames = 0
    node_cfg = [{
        "node_id": o["node_id"], "segment_id": str(o["segment"].id),
        "prompt": o["prompt"], "duration_sec": float(o["duration_sec"]),
        "from_prev": bool(o["from_prev"]),
    } for o in ordered]
    provider_cfg = {
        "board_id": str(board.id),
        "nodes": node_cfg,
        "timeline": timeline_json,
        "global_prompt": str(cfg.get("global_prompt") or "").strip(),
        "task_type": task_type_value,
        "model_id": str(model.id),
        "res": res, "ratio": ratio,
        "width": width, "height": height, "ref_max_size": ref_max_size,
        "total_frames": total_frames,
        "frame_rate": fps,
        "steps": cfg.get("steps"), "sampler": str(cfg.get("sampler") or "res_multistep"),
        "scheduler": str(cfg.get("scheduler") or "simple"), "cfg": cfg.get("cfg"), "seed": cfg.get("seed"),
        "shift_video": cfg.get("shift_video"), "shift_audio": cfg.get("shift_audio"),
        "context_enabled": context_enabled, "context_frames": context_frames,
        "scheme_node_id": str(cfg.get("scheme_node_id") or "").strip() or None,
    }

    task = Task(
        project_id=board.project_id,
        type=TaskType.director_generate,
        target_type="canvas_board", target_id=board.id,
        status=TaskStatus.pending,
        model_id=model.id,
        provider_task_id=json.dumps(provider_cfg, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from app.tasks.canvas_director_generate import canvas_director_generate

    canvas_director_generate.delay(str(task.id))
    return task, [n["node_id"] for n in node_cfg], total_frames
