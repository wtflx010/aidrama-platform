"""视频超分任务（ComfyUI 逐帧 SR）：分块处理 → 帧数/时长保真 → 混回原音轨。支持两类目标：
- video_clip（分镜原片，2026-08-23）：取超清版 clip 与其原片（upscale_of_id）。
- video_draft（AI 视频页签二采，2026-08-25）：取二采行与其来源草稿（base_draft_id），
  SR 结果落 video_drafts/{目标draft.id}/draft_sr.mp4，回写目标 draft.video_url/status。
两者流程一致：分块 SR → 拼接 → 帧数/时长保真 → 混回原音轨 → 落盘各自目录。

流程：
1. 源（原片/草稿视频）须 succeeded 且本地文件存在。
2. ffprobe 源：帧率 / 总帧数 / 时长 / 是否有音轨 / 宽高（草稿无宽高列 → 从文件取）。
3. 按 _CHUNK_FRAMES=90 帧分块：ffmpeg 精确切帧（select 起始帧区间 + setpts 回归零基线，
   无音频，libx264 恒定编码参数）→ 逐块上传 ComfyUI → upscaleVideo 工作流（tier 决定
   4x+抑晕 / 2x 模型，ImageScale 归一到目标宽高，显式源帧率）→ 下载块结果。
4. concat 无失真拼接（-c copy，失败回退重编码）→ 帧数校验：短则 tpad 复制末帧补齐、
   长则 -frames:v 截断，以 -r 源帧率 + 源时长封口 —— 杜绝帧率/时长漂移与末帧异常。
5. 混回原音轨（有则提取 AAC 复用，无则仅画面）→ 音频淡入（与生成链路一致）→
   落盘 videos/{clip.id}/clip.mp4 或 video_drafts/{draft.id}/draft_sr.mp4，置 succeeded。

显存约束：4x-UltraSharp 在 16G 显卡单批 90 帧安全（5376×3072 fp16 tensor ~3.4G），
更长片段分块串行，天然防 OOM。
"""
import json
import logging
import math
import os
import subprocess

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.media import MediaStatus, VideoClip
from app.models.model_config import Model
from app.models.task import Task, TaskStatus
from app.models.video_draft import VideoDraft
from app.providers.errors import ProviderError, map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.upscale_service import target_dims
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    heartbeat_guard,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 每块最多帧数（16G 显存 4x-UltraSharp 安全上限）
_CHUNK_FRAMES = 90
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 1800  # 单块最长 30 分钟


def _ffprobe(path: str) -> tuple[float, int, float, bool, int, int]:
    """返回 (fps, 帧数, 时长秒, 是否有音轨, 宽, 高)。"""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=r_frame_rate,nb_frames,codec_type,width,height", "-show_entries",
            "format=duration", "-of", "json", path,
        ],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise ProviderError("ffprobe 失败: " + (out.stderr or "")[:300])
    try:
        d = json.loads(out.stdout or "{}")
    except Exception:
        d = {}
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    rate = str(v.get("r_frame_rate") or "24/1")
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / float(den) if float(den or 1) else 24.0
    else:
        fps = float(rate or 24)
    frames = int(v.get("nb_frames") or 0)
    dur = float((d.get("format") or {}).get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    width = int(v.get("width") or 0)
    height = int(v.get("height") or 0)
    if frames <= 0 and dur > 0:
        frames = max(1, int(round(dur * fps)))
    if fps <= 0 or fps > 120:
        fps = 24.0
    return fps, frames, dur, has_audio, width, height


def _run_ffmpeg(args: list[str], timeout: int = 900) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise ProviderError("ffmpeg 失败: " + (r.stderr or b"").decode(errors="replace")[-400:])


def _upload_to_comfy(endpoint: str, local_path: str, tag: str = "") -> dict:
    """把本地临时视频块上传到 ComfyUI input 目录（复用 /upload/image 不校验内容类型）。

    tag 必须传唯一标识（如 clip.id / draft.id）：批量超分并发（Celery threads concurrency=3）时，
    若上传文件名不区分任务，不同任务的同名上传会在 ComfyUI input 目录互相覆盖
    （overwrite=true），VHS_LoadVideo 执行时多个工作流读到同一份输入 → 产物串台
    （2026-08-23 事故：三个分镜的超分产物画面完全相同 + 音画错配）。
    """
    base = os.path.basename(local_path).replace(".", "_")
    name = f"ups_{tag}_{base}.mp4" if tag else f"ups_{base}.mp4"
    with open(local_path, "rb") as f:
        r = httpx.post(
            f"{endpoint}/upload/image",
            files={"image": (name, f, "video/mp4")},
            data={"overwrite": "true", "type": "input"},
            timeout=300,
        )
    r.raise_for_status()
    return r.json()  # {name, subfolder, type}


@celery_app.task(name="upscale_video", bind=True)
def upscale_video(self, task_id: str):
    db = SessionLocal()
    db.rollback()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        tt = task.target_type
        if tt == "video_draft":
            # AI 视频页签超分（2026-08-25 就地覆盖）：目标即草稿本身（overwrite），
            # SR 结果回写同一行、删除旧产物；兼容历史二采行（base_draft_id 指向源）。
            target = db.get(VideoDraft, task.target_id)
            if target is None:
                return
            src = db.get(VideoDraft, target.base_draft_id) if target.base_draft_id else target
            is_draft = True
        else:
            target = db.get(VideoClip, task.target_id)
            if target is None:
                return
            src = db.get(VideoClip, target.upscale_of_id) if target.upscale_of_id else None
            is_draft = False
        if src is None or src.status != MediaStatus.succeeded or not src.video_url:
            raise ProviderError("原片不存在或未生成成功，无法超分")
        try:
            cfg = json.loads(task.provider_task_id or "{}")
            tier = str(cfg.get("tier") or "4x").lower()
        except Exception:
            tier = "4x"
        # 兼容旧 recover 语义：本任务是超分专用，provider_task_id 是配置而非 prompt id
        model = db.get(Model, task.model_id)
        if model is None:
            raise ProviderError("模型不存在")
        provider = ProviderRegistry.for_model(model)

        update_task(db, task_id, status=TaskStatus.running, progress=5)
        target.status = MediaStatus.running
        db.commit()

        # 2026-08-25 卡死回收事故修复：分块 SR 之后的本地保真处理（concat/回退重编码/
        # tpad/混音/淡入等同步 ffmpeg 最长 15 分钟）与分块上传（httpx 最长 5 分钟）全程
        # 无心跳——任何一步超过 reclaim 阈值(5min)都会被误判卡死回收（事故现场：远程块
        # 已全部完成 progress=90、本地后处理期间心跳停摆，任务被直接标 failed）。
        # heartbeat_guard 用独立线程覆盖整个重处理区间；reclaim 只应回收真正死亡的 worker。
        with heartbeat_guard(str(task_id)):

            src_local = os.path.join(settings.media_dir, src.video_url.split("/static/media/", 1)[1])
            if not os.path.exists(src_local):
                raise ProviderError("原片文件不存在: " + src_local)
            fps, total_frames, dur, has_audio, src_w, src_h = _ffprobe(src_local)
            # 32-bit 索引上限守卫（2026-08-25 二采冒烟踩中）：
            # 4x-UltraSharp 单批 90 帧的中间张量（源 4× 尺寸 ×3 通道 ×90 帧）须 < 2^31。
            # 安全源像素 ≈ 7.96e6/16 ≈ 4.97e5，即仅 ≤480p 类源能用 4x；更大源自动降 2x
            #（2x 中间张量 源2× ×3×90：768p→4.13e6 ✓），确保 ImageBlur pad 不炸。
            if tier == "4x" and src_w and src_h and src_w * src_h > 520_000:
                logger.info("[upscale] 源 %sx%s 超 480p 类，4x 触发 32-bit 索引上限，自动降档 2x task=%s",
                            src_w, src_h, task_id)
                tier = "2x"
            # 草稿无宽高列 → 以文件实测宽高归一目标（对齐 existing 的 1080p 标准）
            tw, th = (target.width, target.height) if not is_draft else target_dims(src_w, src_h, tier)
            logger.info("[upscale] 开始超分 task=%s type=%s target=%s src=%s tier=%s %sx%s@%g fps frames=%d dur=%.2fs audio=%s -> %sx%s",
                        task_id, tt, target.id, src.id, tier, src_w, src_h, fps, total_frames, dur, has_audio, tw, th)

            tmp_dir = os.path.join(settings.media_dir, "upscale_tmp", str(target.id))
            os.makedirs(tmp_dir, exist_ok=True)
            chunks = max(1, math.ceil(total_frames / _CHUNK_FRAMES))
            out_local_paths: list[str] = []
            try:
                for ci in range(chunks):
                    start = ci * _CHUNK_FRAMES
                    n = min(_CHUNK_FRAMES, total_frames - start)
                    chunk_src = os.path.join(tmp_dir, f"src_{ci}.mp4")
                    # 精确切帧：select 起始/结束帧区间 + setpts 回归零基线，恒定编码参数
                    _run_ffmpeg([
                        "-i", src_local,
                        "-vf", f"select=gte(n\\,{start})*lt(n\\,{start + n}),setpts=N/FRAME_RATE/TB",
                        "-an", "-r", str(fps), "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", chunk_src,
                    ])
                    up = _upload_to_comfy(provider._endpoint(), chunk_src, tag=str(target.id))  # noqa: SLF001 同 provider 约定；tag 防并发上传覆盖
                    handle = provider.upscaleVideo(
                        up.get("name"), tier=tier,
                        target_width=tw, target_height=th,
                        src_fps=fps, duration=dur,
                        prefix=f"upscale/{target.id}/c{ci}",
                    )
                    update_task(db, task_id, progress=8 + int(ci / chunks * 80))
                    res = run_with_polling(
                        db, task_id, provider, handle,
                        poll_interval=_POLL_INTERVAL, timeout=_POLL_TIMEOUT,
                    )
                    if not res.videoUrl:
                        raise ProviderError("超分任务完成但未返回视频")
                    loc_url = download_to_local(
                        res.videoUrl, subdir=f"upscale_tmp/{target.id}",
                        filename=f"out_{ci}.mp4", task_id=task_id,
                    )
                    out_local_paths.append(os.path.join(settings.media_dir, loc_url.split("/static/media/", 1)[1]))
                    logger.info("[upscale] 块 %d/%d 完成 task=%s", ci + 1, chunks, task_id)
                    update_task(db, task_id, progress=12 + int((ci + 1) / chunks * 78))
            finally:
                # 清理源切块（upload 到 ComfyUI 的副本不删，量小）
                for ci in range(chunks):
                    p = os.path.join(tmp_dir, f"src_{ci}.mp4")
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            if not out_local_paths:
                raise ProviderError("超分未产出任何块")

            # ── 拼接 + 帧数/时长保真 + 音轨混回 ──
            list_file = os.path.join(tmp_dir, "list.txt")
            with open(list_file, "w", encoding="utf-8") as f:
                f.write("".join(f"file '{p}\n" for p in out_local_paths))
            concat_path = os.path.join(tmp_dir, "concat.mp4")
            try:
                _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", concat_path], timeout=600)
            except ProviderError:
                logger.info("[upscale] concat 直接拷贝失败，回退重编码 task=%s", task_id)
                _run_ffmpeg(
                    ["-f", "concat", "-safe", "0", "-i", list_file,
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
                     concat_path], timeout=900,
                )
            # 帧数校验：短则 tpad 复制末帧、长则截断，最终 -r 源帧率 + -t 源时长封口
            _fps, _cur_frames, _cur_dur, _has, _, _ = _ffprobe(concat_path)
            base_args = ["-i", concat_path, "-an", "-r", str(fps), "-c:v", "libx264",
                         "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            if _cur_frames < total_frames:
                pad_dur = (total_frames - _cur_frames) / fps
                args = ["-vf", f"tpad=stop_mode=clone:stop_duration={pad_dur:.6f}", *base_args]
            else:
                args = [*base_args, "-frames:v", str(total_frames)]
            base_path = os.path.join(tmp_dir, "base.mp4")
            _run_ffmpeg(args + [f"-t", f"{dur:.6f}" if dur > 0 else f"{total_frames / fps:.6f}", base_path], timeout=900)

            # 混回原音轨（提取 AAC；无音轨则仅画面）
            audio_path = os.path.join(tmp_dir, "audio.m4a")
            if has_audio:
                try:
                    _run_ffmpeg(["-i", src_local, "-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", audio_path], timeout=300)
                except ProviderError:
                    audio_path = None
            else:
                audio_path = None

            if is_draft:
                final_dir = os.path.join(settings.media_dir, "video_drafts", str(target.id))
                final_path = os.path.join(final_dir, "draft_sr.mp4")
                final_url = f"{settings.static_base_url}/media/video_drafts/{target.id}/draft_sr.mp4"
            else:
                final_dir = os.path.join(settings.media_dir, "videos", str(target.id))
                final_path = os.path.join(final_dir, "clip.mp4")
                final_url = f"{settings.static_base_url}/media/videos/{target.id}/clip.mp4"
            os.makedirs(final_dir, exist_ok=True)
            if audio_path and os.path.exists(audio_path):
                _run_ffmpeg([
                    "-i", base_path, "-i", audio_path,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-t", f"{dur:.6f}" if dur > 0 else f"{total_frames / fps:.6f}",
                    "-shortest", final_path,
                ], timeout=600)
            else:
                _run_ffmpeg(["-i", base_path, "-c", "copy", final_path], timeout=300)
            from app.utils.media import apply_audio_fade_in
            apply_audio_fade_in(final_path)

            # 终校：帧数与时长必须与原片一致
            _ofps, _oframes, _odur, _, _, _ = _ffprobe(final_path)
            if _oframes != total_frames:
                logger.warning("[upscale] 终校帧数不一致 src=%d out=%d，重新 tpad/截断", total_frames, _oframes)
                if _oframes < total_frames:
                    pad_dur = (total_frames - _oframes) / fps
                    _run_ffmpeg(["-i", final_path, "-vf", f"tpad=stop_mode=clone:stop_duration={pad_dur:.6f}",
                                 "-c:v", "libx264", "-crf", "18", "-r", str(fps), "-frames:v", str(total_frames),
                                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", final_path + ".fix.mp4"], timeout=600)
                    os.replace(final_path + ".fix.mp4", final_path)
                else:
                    _run_ffmpeg(["-i", final_path, "-frames:v", str(total_frames),
                                 "-c:v", "libx264", "-crf", "18", "-r", str(fps),
                                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", final_path + ".fix.mp4"], timeout=600)
                    os.replace(final_path + ".fix.mp4", final_path)

        if is_draft:
            # 就地覆盖：删除旧产物文件（保留新成品 draft_sr.mp4），行内升级 1080p。
            # 用户决策（2026-08-25）：超分应覆盖当前视频，不留超分前文件、不建新行。
            try:
                for fn in os.listdir(final_dir):
                    fpath = os.path.join(final_dir, fn)
                    if os.path.isfile(fpath) and fn.endswith(".mp4") and fn != "draft_sr.mp4":
                        os.remove(fpath)
                        logger.info("[upscale] 删除被覆盖旧文件 %s", fpath)
            except OSError as e:
                logger.warning("[upscale] 清理旧文件失败（不影响成品）: %s", e)
            target.resolution = "1080p"
            target.base_draft_id = None
            target.duration = int(round(dur)) if dur > 0 else int(round(total_frames / fps))
        else:
            target.duration = dur if dur > 0 else (total_frames / fps)
        target.video_url = final_url
        target.status = MediaStatus.succeeded
        target.error = None
        update_task(db, task_id, status=TaskStatus.succeeded, progress=100,
                    result_url=final_url, finished_at=now())
        db.commit()
        logger.info("[upscale] 超分完成 target=%s src=%s %sx%s -> %sx%s 时长=%.2fs type=%s",
                    target.id, src.id, src_w, src_h, tw, th, dur, tt)
    except TaskCancelledError:
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        try:
            if task.target_type == "video_draft":
                obj = db.get(VideoDraft, task.target_id)
                # 就地覆盖失败：原视频仍在，只失败任务、不把草稿标 failed（可重试）
                if obj is not None and obj.base_draft_id:
                    obj.status = MediaStatus.failed
                    obj.error = msg
                    db.commit()
            else:
                obj = db.get(VideoClip, task.target_id)
                if obj is not None:
                    obj.status = MediaStatus.failed
                    obj.error = msg
                    db.commit()
        except Exception:
            db.rollback()
        logger.warning("[upscale] 任务失败 task=%s err=%s", task_id, msg)
    finally:
        db.close()
