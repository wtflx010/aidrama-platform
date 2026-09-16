"""项目页签连续长片任务（2026-09）：真实提交 166 或 mock 离线模拟。

真实链路：ComfyUIProvider.director_generate（探测 AIMixer 导演台节点 → 上传参考图 →
提交工作流）→ run_with_polling → 下载整片 → 回写 episode.continuous_film_*。
mock 链路（DIRECTOR_MODE=mock，166 关机时的开发通道）：本地生成占位 mp4，
全链路（任务/轮询/回写）与真实一致，仅不走 166。
"""
import json
import logging
import os
import random
import shutil
import subprocess
import time

from app.config import settings
from app.database import SessionLocal
from app.models.media import MediaStatus, VideoClip
from app.models.project import Episode
from app.models.segment import Segment
from app.models.task import Task, TaskStatus
from app.models.model_config import ModelType
from app.tasks.base import TaskCancelledError, download_to_local, now, run_with_polling, update_task
from app.tasks.celery_app import celery_app
from app.tasks.canvas_director_generate import _make_mock_video

logger = logging.getLogger(__name__)


def _crop_film_to_segments(db, task_id: str, cfg: dict, full_path: str) -> list[dict]:
    """把整片按各分镜切点（来自 timeline 帧数）裁成片段，回挂各分镜 VideoClip。

    cfg['timeline'] 的 segments 是长度(帧)计划，fps 换算秒。每个 cfg['segments'][i] 的
    segment_id 与 timeline segments[i] 一一对应。返回逐段裁剪信息（start_sec/end_sec/length）。
    """
    subdir = f"project_director/{task_id}"
    try:
        tl = json.loads(cfg.get("timeline") or "{}")
        plan = tl.get("segments") or []
    except Exception:  # noqa: BLE001
        return []
    fps = float(cfg.get("frame_rate") or 24) or 24.0
    width = int(cfg.get("width") or 832)
    height = int(cfg.get("height") or 480)
    clip_segs = cfg.get("segments") or []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not os.path.exists(full_path) or not plan:
        return []

    cuts = []
    cur = 0.0
    for i, p in enumerate(plan):
        length = int(p.get("length") or 0)
        start_sec = cur / fps
        end_sec = (cur + length) / fps
        cur += length
        cuts.append({"idx": i, "length": length, "start_sec": start_sec, "end_sec": end_sec})

    out = []
    for c in cuts:
        meta_seg = clip_segs[c["idx"]] if c["idx"] < len(clip_segs) else {}
        sid = meta_seg.get("segment_id")
        seg = db.get(Segment, sid) if sid else None
        if seg is None or c["end_sec"] <= c["start_sec"]:
            continue
        fname = f"seg{c['idx'] + 1}.mp4"
        out_path = os.path.join(settings.media_dir, subdir, fname)
        try:
            cmd = [
                ffmpeg, "-y", "-ss", f"{c['start_sec']:.3f}", "-i", full_path,
                "-t", f"{(c['end_sec'] - c['start_sec']):.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-pix_fmt", "yuv420p", out_path,
            ]
            subprocess.run(cmd, capture_output=True, timeout=600, check=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("裁切分镜 %s 失败: %s", sid, exc)
            continue
        clip_url = f"{settings.static_base_url}/media/{subdir}/{fname}"
        import uuid as _uuid
        clip = VideoClip(
            segment_id=seg.id,
            keyframe_id=None,
            prompt=meta_seg.get("prompt"),
            num_frames=int(c["length"]),
            frame_rate=int(fps),
            width=width, height=height,
            video_url=clip_url,
            duration=round((c["end_sec"] - c["start_sec"]), 2),
            status=MediaStatus.succeeded,
            model_id=cfg.get("model_id"),
            task_id=_uuid.UUID(str(task_id)) if task_id else None,
            is_upscaled=False,
        )
        db.add(clip)
        db.flush()
        out.append({"segment_id": str(seg.id), "video_url": clip_url, "start_sec": c["start_sec"], "end_sec": c["end_sec"]})
    db.commit()
    return out


def _resolve_tts_model(db):
    """取一个启用的 TTS（CosyVoice）模型；无则返回 None。"""
    from app.services.keyframe_service import _resolve_model
    try:
        return _resolve_model(db, None, ModelType.tts, "voice")
    except Exception:  # noqa: BLE001
        return None


def _overlay_dialogue(db, task_id: str, cfg: dict, cuts: list[dict], full_path: str):
    """方案A：按各分镜对白生成「角色音色 + 情绪」TTS，按切点混入整片并生成字幕。

    cuts: _crop_film_to_segments 产出（segment_id/start_sec/end_sec/idx）。
    返回 (final_film_url, subtitle_url|None)；无对白/合成失败则返回 (full_path, None)。
    """
    from app.providers.registry import ProviderRegistry
    from app.services import character_voice_service
    from app.tasks.base import bytes_to_local, probe_duration

    model = _resolve_tts_model(db)
    if model is None:
        logger.warning("未找到 TTS 模型，跳过对白叠加（画面仍静音）")
        return full_path, None
    provider = ProviderRegistry.for_model(model)
    subdir = f"project_director/{task_id}"
    cut_by_seg = {str(c["segment_id"]): c for c in cuts}

    lines = []
    for meta_seg in (cfg.get("segments") or []):
        sid = meta_seg.get("segment_id")
        seg = db.get(Segment, sid) if sid else None
        if seg is None:
            continue
        cut = cut_by_seg.get(str(sid))
        if not cut:
            continue
        dialogs = seg.dialogue_lines or []
        pos = float(cut["start_sec"])
        span = float(cut["end_sec"]) - pos
        for dl in dialogs:
            text = (dl.get("text") or "").strip()
            if not text:
                continue
            cid = dl.get("character_id")
            vp = character_voice_service.get_character_voice_profile(db, cid) if cid else {}
            opts = character_voice_service._build_tts_opts(vp, emotion=dl.get("emotion"))
            try:
                handle = provider.synthesize(text, opts.voice or "default", opts)
                b64 = (handle.meta or {}).get("audio_bytes_b64")
                if not b64:
                    continue
                audio_url = bytes_to_local(
                    b64, subdir=f"{subdir}/voice", filename=f"vl_{len(lines)}.{handle.meta.get('format', 'mp3')}"
                )
                audio_path = audio_url.replace(f"{settings.static_base_url}/media/", f"{settings.media_dir}/")
                dur = probe_duration(audio_path) or (len(text) / 4.0)
                lines.append({"start": pos, "audio": audio_path, "text": text, "dur": dur})
                pos += dur + 0.15
                if pos > float(cut["end_sec"]) - 0.3:
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning("对白合成失败（%s）: %s", str(sid)[:8], exc)
    if not lines:
        return full_path, None

    # ffmpeg：把各段对白按起始时间迟延后 amix 成一条音轨，mux 到画面上
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return full_path, None
    out_path = os.path.join(settings.media_dir, subdir, "full_dialogue.mp4")
    cmd = [ffmpeg, "-y", "-i", full_path]
    for ln in lines:
        cmd += ["-i", ln["audio"]]
    fc = []
    aux = []
    for i, ln in enumerate(lines):
        ms = int(round(ln["start"] * 1000))
        fc.append(f"[{i + 1}:a]adelay={ms}:all=1[a{i}]")
        aux.append(f"[a{i}]")
    fc.append("".join(aux) + f"amix=inputs={len(lines)}:normalize=0[aout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-shortest", out_path]
    try:
        subprocess.run(cmd, capture_output=True, timeout=900, check=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("对白混入失败: %s", exc)
        return full_path, None

    # 字幕 SRT
    srt_path = os.path.join(settings.media_dir, subdir, "subtitles.srt")
    try:
        with open(srt_path, "w", encoding="utf-8") as fh:
            for n, ln in enumerate(lines, 1):
                def ts(s):
                    h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
                    return f"{h:02d}:{m:02d}:{sec:06.3f}"
                fh.write(f"{n}\n{ts(ln['start'])} --> {ts(ln['start'] + ln['dur'])}\n{ln['text']}\n\n")
    except Exception:  # noqa: BLE001
        pass
    final_url = f"{settings.static_base_url}/media/{subdir}/full_dialogue.mp4"
    sub_url = f"{settings.static_base_url}/media/{subdir}/subtitles.srt" if os.path.exists(srt_path) else None
    return final_url, sub_url


@celery_app.task(name="project_director_generate", bind=True)
def project_director_generate(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        cfg = json.loads(task.provider_task_id or "{}")
        episode = db.get(Episode, cfg.get("episode_id"))
        if episode is None:
            raise ValueError("episode 不存在")
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=3)
        episode.continuous_film_status = "running"
        db.commit()

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
                raise ValueError("连续长片模型不存在或已停用")
            provider = ProviderRegistry.for_model(model, resolution=str(cfg.get("res") or "") or None)
            if not provider.director_node_available():
                raise ValueError(
                    "166 ComfyUI 未安装 MiniMax H3 Director 插件（连续长片模式不可用）。"
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
                high_quality=bool(cfg.get("high_quality")),
            )
            saved = json.loads((db.get(Task, task_id).provider_task_id) or "{}") if db.get(Task, task_id) else {}
            saved["prompt_id"] = handle.providerTaskId
            t = db.get(Task, task_id)
            if t is not None:
                t.provider_task_id = json.dumps(saved, ensure_ascii=False)
                t.poll_url = handle.pollUrl
                t.provider = handle.provider
                db.commit()
            result = run_with_polling(
                db, task_id, provider, handle,
                poll_interval=settings.celery_video_poll_interval,
                timeout=settings.celery_video_timeout,
            )
            if not result.videoUrl:
                raise ValueError("连续长片任务完成但未返回视频 URL")
            local_url = download_to_local(
                result.videoUrl, subdir=f"project_director/{task_id}", filename="full.mp4", task_id=str(task_id),
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

        # ── 回写集级整片 ──────────────────────────────────────
        meta = {
            "note": note,
            "task_type": cfg.get("task_type"),
            "context_enabled": bool(cfg.get("context_enabled", True)),
            "context_frames": int(cfg.get("context_frames") or 22),
            "segments": cfg.get("segments") or [],
            "res": cfg.get("res"), "ratio": cfg.get("ratio"),
            "width": cfg.get("width"), "height": cfg.get("height"),
        }
        duration = round(total_frames / fps, 2) if total_frames and fps else None
        # 长片按切点裁成分镜片段并回挂各分镜（仅真实出片；166 离线 mock 无真实切点）
        if mode == "real":
            full_path = os.path.join(settings.media_dir, f"project_director/{task_id}", "full.mp4")
            cuts = _crop_film_to_segments(db, str(task_id), cfg, full_path)
            meta["segment_cuts"] = cuts
            # 用 H3 原声：对白/旁白已写进提示词，H3 用原声让角色开口；不再叠加 TTS
        episode.continuous_film_url = local_url
        episode.continuous_film_duration = duration
        episode.continuous_film_meta = meta
        episode.continuous_film_status = "done"
        episode.video_status = "done"
        t2 = db.get(Task, task_id)
        if t2 is not None:
            t2.result_url = local_url
            t2.finished_at = now()
            t2.progress = 100
            t2.status = TaskStatus.succeeded
        db.commit()
    except TaskCancelledError:
        ep = db.get(Episode, json.loads(db.get(Task, task_id).provider_task_id or "{}").get("episode_id")) if db.get(Task, task_id) else None
        if ep is not None:
            ep.continuous_film_status = "cancelled"
            db.commit()
        update_task(db, task_id, status=TaskStatus.cancelled)
    except Exception as exc:  # noqa: BLE001
        logger.exception("项目连续长片任务失败: %s", task_id)
        cur = db.get(Task, task_id)
        if cur is not None and cur.status == TaskStatus.cancelled:
            # 用户已取消：保持 cancelled，不覆盖为 failed；回写幕状态
            ep = db.get(Episode, json.loads(cur.provider_task_id or "{}").get("episode_id")) if cur else None
            if ep is not None:
                ep.continuous_film_status = "cancelled"
                db.commit()
            return
        ep = db.get(Episode, json.loads(db.get(Task, task_id).provider_task_id or "{}").get("episode_id")) if db.get(Task, task_id) else None
        if ep is not None:
            ep.continuous_film_status = "failed"
            db.commit()
        update_task(db, task_id, status=TaskStatus.failed, error=str(exc))
    finally:
        db.close()
