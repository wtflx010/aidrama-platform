"""项目页签「一集一条连续长片」业务服务（2026-09）。

与画布导演台(canvas_director_service)同后端提交协议，但输入从「画布节点」换成
「项目的整集分镜序列」，全程不走画布。依赖 166 上的 AIMixer/ComfyUI_MiniMaxH3_Director
（DIRECTOR_MODE=real）或 mock（166 关机离线验证）。

产物 = 一集一条连续整片（AIMixer 段间运动/音频续拍），回写到 episode.continuous_film_*。
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Episode
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.constants import CHARS_PER_SEC
from app.services.video_pipeline.prompts import is_face_talking_shot, prompt_with_speech
from app.services.canvas_director_service import (
    _collect_global_refs,
    _resolve_dims,
    _resolve_director_model,
    build_director_timeline,
)
from app.services.longfilm_prompts import build_episode_global_prompt, compose_segment_prompt

logger = logging.getLogger(__name__)

MAX_DIRECTOR_DURATION_SEC = 15.0


def _segment_duration_sec(segment, default: float = 5.0) -> float:
    d = getattr(segment, "duration", None)
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = None
    return d if d and d > 0 else default


def _ordered_segments(db: Session, episode_id, segment_ids=None):
    """按 index 取整集分镜；segment_ids 指定则仅取这些（仍按 index 排序）。"""
    segs = db.scalars(
        select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.index)
    ).all()
    if segment_ids:
        ids = {str(s) for s in segment_ids}
        segs = [s for s in segs if str(s.id) in ids]
    return list(segs)


def _is_face_talking_shot(seg) -> bool:
    """镜头是否为「人物正面/近景·口型可见」的说话特写。

    2026-09 收敛：逻辑点在 video_pipeline.prompts.is_face_talking_shot。"""
    return is_face_talking_shot(seg)


def _prompt_with_speech(seg, prompt: str) -> str:
    """把该分镜的「对白 / 旁白」写进画面提示词，让 H3 按镜头实际情况说。

    2026-09 收敛：逻辑点在 video_pipeline.prompts.prompt_with_speech。"""
    return prompt_with_speech(seg, prompt)



def generate(db: Session, episode_id, payload):
    """连续长片入口：校验 → 组装时间轴 → 建任务派发。返回 (task, segment_ids, total_frames)。"""
    from app.tasks.project_director_generate import project_director_generate

    episode = db.get(Episode, episode_id)
    if episode is None:
        raise ValueError("episode 不存在")

    segments = _ordered_segments(db, episode_id, getattr(payload, "segment_ids", None))
    if not segments:
        raise ValueError("该幕没有可用的分镜，无法连续出片")

    cfg = dict(payload.config or {})
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
    global_prompt = (
        str(payload.global_prompt or "").strip()
        or str(cfg.get("global_prompt") or "").strip()
        or build_episode_global_prompt(episode, segments)
    )

    # Turbo 档位 → SigmaShift 双流 shift + 步数预设（对齐 generate_video 的 Turbo 映射；显式 steps 优先）
    _turbo = str(cfg.get("turbo") or "").strip().lower()
    _turbo_preset = {
        "high": (9.0, 2.0, 8),
        "mid": (12.0, 3.0, 10),
        "low": (16.0, 4.0, 14),
    }.get(_turbo)
    _steps = cfg.get("steps")
    _shift_video = cfg.get("shift_video")
    _shift_audio = cfg.get("shift_audio")
    if _turbo_preset:
        _shift_video, _shift_audio = _turbo_preset[0], _turbo_preset[1]
        if _steps in (None, "", "null"):
            _steps = _turbo_preset[2]

    # 逐镜：prompt（含连续长片承接头）/ duration / from_prev
    rows_for_tl = []
    node_cfg = []
    for i, seg in enumerate(segments):
        prev_seg = segments[i - 1] if i > 0 else None
        # 用 H3 原声：把对白/旁白文本写进提示词，让角色按台词开口说话（不剥对白、不叠加TTS）
        prompt = _prompt_with_speech(seg, compose_segment_prompt(seg, None, prev_seg))
        duration = _segment_duration_sec(seg)
        from_prev = i != 0
        rows_for_tl.append((seg, duration, prompt, from_prev))
        node_cfg.append({
            "segment_id": str(seg.id),
            "index": seg.index,
            "prompt": prompt,
            "duration_sec": float(duration),
            "from_prev": bool(from_prev),
        })

    # 公共参考图（跨镜去重）；纯文生不收集
    enable_refs = bool(cfg.get("enable_common_refs", True))
    collect_refs = _collect_global_refs(db, list(segments)) if enable_refs else []

    is_r2v = ("r2v" in task_type_value.lower()) or ("reference to video" in task_type_value.lower())
    # r2v 但无任何参考图 → 自动降级 t2v 纯文生（避免空 refs 提交 166 行为不可控）
    if is_r2v and not collect_refs:
        task_type_value = "t2v — 文生视频(Text to Video)"

    timeline_json = build_director_timeline(
        rows_for_tl,
        global_refs=collect_refs,
        global_prompt=global_prompt,
        task_type_value=task_type_value,
        fps=fps, width=width, height=height, ref_max_size=ref_max_size,
        context_enabled=context_enabled, context_frames=context_frames,
        # audio_mode=generate：H3 正常出片，对白叠加阶段仅输出对白音轨（自动无唱歌）
    )

    model = _resolve_director_model(db, cfg.get("model_id"))
    if model is None:
        raise ValueError("未找到可用的 MiniMax H3 ComfyUI 视频模型（连续长片必需）")

    try:
        total_frames = sum(int(x["length"]) for x in json.loads(timeline_json).get("segments", []))
    except Exception:  # noqa: BLE001
        total_frames = 0

    provider_cfg = {
        "episode_id": str(episode.id),
        "project_id": str(episode.project_id),
        "segments": node_cfg,
        "timeline": timeline_json,
        "global_prompt": global_prompt,
        "task_type": task_type_value,
        "model_id": str(model.id),
        "res": res, "ratio": ratio,
        "width": width, "height": height, "ref_max_size": ref_max_size,
        "total_frames": total_frames,
        "frame_rate": fps,
        "steps": _steps, "sampler": str(cfg.get("sampler") or "res_multistep"),
        "scheduler": str(cfg.get("scheduler") or "simple"), "cfg": cfg.get("cfg"), "seed": cfg.get("seed"),
        "shift_video": _shift_video, "shift_audio": _shift_audio,
        "turbo": _turbo or "", "high_quality": bool(cfg.get("high_quality")),
        "context_enabled": context_enabled, "context_frames": context_frames,
    }

    task = Task(
        project_id=episode.project_id,
        type=TaskType.project_director,
        target_type="episode", target_id=episode.id,
        status=TaskStatus.pending,
        model_id=model.id,
        provider_task_id=json.dumps(provider_cfg, ensure_ascii=False),
    )
    db.add(task)
    episode.continuous_film_status = "pending"
    db.commit()
    db.refresh(task)

    project_director_generate.delay(str(task.id))
    return task, [n["segment_id"] for n in node_cfg], total_frames
