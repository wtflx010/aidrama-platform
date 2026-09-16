"""AI 视频草稿生成任务（Video Lab 页签，2026-08-11）。

按草稿参考素材自动路由（模型已在 generate 阶段按 want_ref 解析并写入 task.model_id）：
- R2V（minimax_ref）：参考图（首帧锚定 + 上传参考图 + 资产封面）≤9 张
  + 参考视频 ≤3 个（LoadVideo → GetVideoComponents 拆帧/音轨），混合注入
  MiniMaxH3ReferenceToVideo；prompt 用 LLM 增强结果（含 <Picture N>/<Video N> 标签）
- FL2VA（minimax）：首尾帧（可选）走 MiniMaxH3ImageToVideo；纯文生无帧走同节点

最终视频下载到 {media_dir}/video_drafts/{draft_id}/，回填 draft.video_url 并置 succeeded。
"""
import json
import logging
import os
import subprocess

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.media import MediaStatus
from app.models.model_config import Model
from app.models.project import Project
from app.models.task import Task, TaskStatus
from app.models.video_draft import VideoDraft
from app.providers.base import VideoOpts
from app.providers.errors import ProviderError, map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.style_service import get_style_video_params
from app.services.video_draft_service import resolve_asset_ref_urls
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 宽高由画面比例推导（imageToVideo 内会按 minimax_res 档位重映射，此处仅用于比例识别）
_RATIO_WH = {
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "1:1": (768, 768),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}

_REF_IMAGE_MAX = 9
_REF_VIDEO_MAX = 3

_FALLBACK_NEGATIVE = (
    "low quality, lowres, blurry, watermark, text, subtitle, captions, logo, "
    "extra person, multiple people, duplicate, warped face, distorted face, "
    "deformed face, facial distortion, face morphing, melting face, "
    "flickering face, unstable face, disfigured face, cross-eyed, "
    "misplaced facial features, oversmoothed skin, plastic skin, "
    "stiff motion, morphing artifacts, flickering, jitter"
)


# ── 2026-08-27 出片后「LTX 原生精修（只精修不放大）」──
# 设计：对 480p/720p 档追加一轮 LTX-2.5 native 重渲（8 步 / denoise 0.22 /
# IC-LoRA 强度 1.0 / euler+simple，不放大、无 guide）。
# ⚠️ 2026-08-27 实证修正：精修对 480p/720p 中远景人脸是【净负优化】（重绘脸
# 被洗软 + 高频噪声塑料感，用户实感"精修后脸更差"，且与踩坑 40/41 机制一致），
# 故默认关闭（VIDEO_REFINE_RESOLUTIONS 默认空）；保留管线与 .env 开关供单档实验。
# 已修复的 ffmpeg -vf 参数顺序 bug（提交 d827dd3）保留——未来开启时仍成立。
_REFINE_POS = (
    "high quality video, refined clear natural facial details, same scene and same character, "
    "sharp clean image, cinematic lighting, 24fps"
)
_REFINE_NEG = (
    "blurry, deformed face, wrong identity, identity shift, ghosting, double exposure, "
    "jpeg artifacts, text, watermark"
)


def _refine_apply_resolutions() -> set[str]:
    """精修适用的分辨率集合。

    默认空 = 关闭（2026-08-27 实证：LTX 重渲在 480p/720p 中远景人脸净负优化，
    重绘脸更软/高频噪声，H3 原生更好，见 H3资源库 09 修正节 + 踩坑 40/41）。
    需要时用 .env VIDEO_REFINE_RESOLUTIONS 显式开启（如 "720p" 单档实验）。
    """
    return {s.strip().lower() for s in (settings.video_refine_resolutions or "").split(",") if s.strip()}


def _ffprobe_refine(path: str):
    """返回 (fps, 总帧数, 时长秒, 是否有音轨)。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=r_frame_rate,nb_frames,codec_type", "-show_entries",
         "format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise ProviderError("ffprobe 失败: " + (out.stderr or "")[:300])
    d = json.loads(out.stdout or "{}")
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    rate = str(v.get("r_frame_rate") or "24/1")
    fps = 24.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        fps = float(num) / float(den) if float(den or 1) else 24.0
    frames = int(v.get("nb_frames") or 0)
    dur = float((d.get("format") or {}).get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if frames <= 0 and dur > 0:
        frames = max(1, int(round(dur * fps)))
    return fps, frames, dur, has_audio


def _ffmpeg_refine(args: list, timeout: int = 900) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise ProviderError("ffmpeg 失败: " + (r.stderr or b"").decode(errors="replace")[-400:])


def _upload_refine_src(endpoint: str, local_path: str, tag: str) -> str:
    """上传草稿到 ComfyUI input（tag 必须含 draft.id，防并发覆盖，约定同超分）。"""
    name = f"refine_{tag}_{os.path.basename(local_path).replace('.', '_')}.mp4"
    with open(local_path, "rb") as f:
        r = httpx.post(
            f"{endpoint}/upload/image",
            files={"image": (name, f, "video/mp4")},
            data={"overwrite": "true", "type": "input"},
            timeout=300,
        )
    r.raise_for_status()
    return r.json().get("name") or name


def _refine_draft_maybe(db, task_id: str, draft, provider, local_path: str) -> str:
    """对刚生成的草稿跑 LTX 原生精修（E2A 参数），成功后就地覆盖 draft.mp4。

    帧数/时长与源不一致时本地 tpad/截断对齐，音轨统一从原片 AAC 重混（同超分范式）；
    任何异常向上抛，由调用方告警并保留原片。
    """
    fps, total_frames, dur, has_audio = _ffprobe_refine(local_path)

    up_name = _upload_refine_src(provider._endpoint(), local_path, str(draft.id))  # noqa: SLF001 同 provider 约定
    handle = provider.refineVideo(
        up_name, prompt=_REFINE_POS, negative=_REFINE_NEG,
        steps=8, denoise=0.22, ic_lora_strength=1.0,
        prefix=f"refine/{draft.id}",
    )
    update_task(db, task_id, progress=88, provider=handle.provider,
                provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl)
    res = run_with_polling(
        db, task_id, provider, handle,
        poll_interval=settings.celery_video_poll_interval,
        timeout=settings.celery_video_timeout,
    )
    if not res.videoUrl:
        raise ProviderError("精修任务完成但未返回视频 URL")
    _url = download_to_local(
        res.videoUrl, subdir=f"refine_tmp/{draft.id}",
        filename="refined.mp4", task_id=task_id,
    )
    tmp_dir = os.path.join(settings.media_dir, "refine_tmp", str(draft.id))
    refined_path = os.path.join(settings.media_dir, _url.split("/static/media/", 1)[1])
    _fps2, frames2, dur2, has2 = _ffprobe_refine(refined_path)
    logger.info("[video_draft] 精修产物帧数=%d 源=%d draft=%s", frames2, total_frames, draft.id)

    # 视频流对齐：帧数短则 tpad 末帧克隆、长则截断，统一 -r 源帧率（纯画面、无音轨）。
    # 2026-08-27 修复：-vf 是输出选项，必须放在 -i <输入> 之后、输出文件之前；
    # 原实现把 -vf 放最前 → ffmpeg 报 "input option applied to output" → 精修每次
    # 静默失败降级原片（480p/720p 草稿精修从未真正生效，见 09 附录排错）。
    fix_path = os.path.join(tmp_dir, "refined_v.mp4")
    vf_args: list[str] = []
    if frames2 < total_frames:
        pad_dur = (total_frames - frames2) / fps
        vf_args = ["-vf", f"tpad=stop_mode=clone:stop_duration={pad_dur:.6f}"]
    _ffmpeg_refine(["-i", refined_path, "-an", "-r", str(fps), *vf_args,
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-frames:v", str(total_frames), fix_path], timeout=900)

    final_ref = fix_path
    if has_audio:
        audio_path = os.path.join(tmp_dir, "audio.m4a")
        _ffmpeg_refine(["-i", local_path, "-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", audio_path], timeout=300)
        final_ref = os.path.join(tmp_dir, "refined_final.mp4")
        _ffmpeg_refine([
            "-i", fix_path, "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", f"{dur:.6f}" if dur > 0 else f"{total_frames / fps:.6f}",
            "-shortest", final_ref,
        ], timeout=600)

    from app.utils.media import apply_audio_fade_in
    apply_audio_fade_in(final_ref)
    # 就地覆盖 draft.mp4（URL 不变，与超分「就地覆盖」语义一致；失败保留原片）
    os.replace(final_ref, local_path)
    logger.info("[video_draft] 草稿精修完成并覆盖 draft=%s src=%d->%d 帧", draft.id, total_frames, frames2)
    return local_path


@celery_app.task(name="generate_video_draft", bind=True)
def generate_video_draft_task(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return  # 任务行已被级联删除
        draft = db.get(VideoDraft, task.target_id)
        if draft is None or draft.project_id != task.project_id:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="视频草稿不存在", finished_at=now(),
            )
            return
        project = db.get(Project, draft.project_id)
        model = db.get(Model, task.model_id) if task.model_id else None
        # 生成档位：草稿级 resolution 优先（480p/720p/768p），否则跟随项目
        # （2026-08-23 二采验证：480p 草稿可显式指定，768p 二采行强制 768p）
        provider = (
            ProviderRegistry.for_model(
                model,
                resolution=(draft.resolution or (project.resolution if project else None)),
            )
            if model else None
        )
        if provider is None:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="视频模型不可用，请先在模型管理启用视频模型", finished_at=now(),
            )
            return
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)

        # 参考素材：路由 R2V / FL2VA 的依据（与 generate 阶段一致：有参考 → R2V）
        ref_image_urls = list(draft.ref_image_urls or [])
        ref_video_urls = list(draft.ref_video_urls or [])
        asset_urls = resolve_asset_ref_urls(db, list(draft.asset_refs or []))
        want_ref = bool(ref_image_urls or ref_video_urls or asset_urls)

        # 最终提示词：LLM 增强优先（R2V 链路含 <Picture N>/<Video N> 标签）
        prompt = (draft.enhanced_prompt or draft.prompt or "").strip()
        if not prompt:
            raise ProviderError("提示词为空，请填写后重试")
        style_params = get_style_video_params(db, project) if project else {}
        # 2026-08-27 慢动作修复（方案A）：/gen/video 直调链路同样追加运镜与动作幅度
        # 约束（H3 提示驱动，不给动作信号容易输出温和运动 → 观感像慢动作，见 09 附录）。
        prompt = prompt + (
            "【运镜与动作】角色动作与镜头运动自然流畅、幅度到位、节奏明快，"
            "与真实生活节奏一致：人物做走路、奔跑、转身、抬手、回头、挥手等动作时"
            "干脆利落、摆幅明显；镜头运动写明方向与速度"
            "（推进/拉远/横移/环绕/跟随，normal 至 fast speed）；"
            "除剧情明确要求的慢镜头外，整体不得出现动作呆滞、迟缓、拖沓"
            "或近似静止的画面，禁止慢动作效果。\n"
        )
        negative = (draft.negative_prompt or _FALLBACK_NEGATIVE).strip()
        if style_params.get("negative_extra"):
            negative = f"{negative}, {style_params['negative_extra']}".strip(", ")

        width, height = _RATIO_WH.get(draft.aspect_ratio, _RATIO_WH["16:9"])
        num_frames = max(5, draft.duration * 24)
        opts = VideoOpts(
            prompt=prompt,
            width=width, height=height,
            num_frames=num_frames, frame_rate=24,
            duration=float(draft.duration),
            negative_prompt=negative,
            shift_video=style_params.get("shift_video"),
            # shift_audio 已从风格档剥离（2026-08-27）：None → capability 铁律（FL2V=6.0 / R2V=3.0）
            shift_audio=None,
            steps=None,
            # 短视频档位：显式下限，避免被 H3 124 帧默认下限拉长（4s=96→节点上对齐）
            min_frames=num_frames,
        )

        if want_ref:
            # R2V：首帧作 ref_image_0 锚定构图 + 上传参考图 + 资产封面；参考视频独立注入
            refs: list[str] = []
            if draft.first_frame_url:
                refs.append(draft.first_frame_url)
            seen = set(refs)
            for u in ref_image_urls + asset_urls:
                if u and u not in seen:
                    seen.add(u)
                    refs.append(u)
            refs = refs[:_REF_IMAGE_MAX]
            handle = provider.imageToVideo(
                draft.first_frame_url, None, opts,
                reference_assets=refs,
                reference_videos=ref_video_urls[:_REF_VIDEO_MAX],
            )
        else:
            # FL2VA / 纯文生：首尾帧直接走节点 first_frame/last_frame
            handle = provider.imageToVideo(
                draft.first_frame_url, draft.last_frame_url, opts,
            )
        update_task(
            db, task_id, provider=handle.provider,
            provider_task_id=handle.providerTaskId,
            poll_url=handle.pollUrl,
            progress=10,
        )

        result = run_with_polling(
            db, task_id, provider, handle,
            poll_interval=settings.celery_video_poll_interval,
            timeout=settings.celery_video_timeout,
        )
        if not result.videoUrl:
            raise ProviderError(
                f"视频任务完成但未返回视频 URL（result_jsonpath 未匹配）。原始响应: {result.raw}"
            )
        local_url = download_to_local(
            result.videoUrl, subdir=f"video_drafts/{draft.id}",
            filename="draft.mp4", task_id=task_id,
        )
        # MiniMax H3 开头自带 ~0.1s 瞬态爆音 → 音频淡入消除
        from app.utils.media import apply_audio_fade_in

        _local = os.path.join(settings.media_dir, local_url.split("/static/media/", 1)[1])
        apply_audio_fade_in(_local)
        # ── 2026-08-27 LTX 原生精修（默认关闭；480p/720p 中远景人脸实测净负，见模块头）──
        if settings.video_refine_enabled and (draft.resolution or "").lower() in _refine_apply_resolutions():
            logger.info("[video_draft] 触发草稿精修 draft=%s res=%s", draft.id, draft.resolution)
            try:
                _local = _refine_draft_maybe(db, task_id, draft, provider, _local)  # 就地覆盖 draft.mp4
            except Exception as _refine_e:  # noqa: BLE001  精修失败不拖垮生成，回退原片
                db.rollback()
                _local = os.path.join(settings.media_dir, local_url.split("/static/media/", 1)[1])
                logger.warning("[video_draft] 草稿精修失败，保留原片 draft=%s: %s", draft.id, _refine_e)
        draft.video_url = local_url
        draft.status = MediaStatus.succeeded
        draft.error = None
        db.commit()
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            finished_at=now(),
        )
        logger.info("[video_draft] 视频生成成功 draft=%s url=%s", draft.id, local_url)
    except TaskCancelledError:
        # 用户取消：不回写 failed（草稿状态由 task_service 回退 pending）
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        _mark_draft_failed(db, task_id, msg)
        logger.warning("[video_draft] 视频生成失败 task=%s: %s", task_id, msg)
    finally:
        db.close()


def _mark_draft_failed(db, task_id: str, msg: str) -> None:
    """任务失败时回写草稿 status=failed + error（不覆盖已回退 pending 的取消场景）。"""
    t = db.get(Task, task_id)
    if t is None:
        return
    d = db.get(VideoDraft, t.target_id)
    if d is not None and d.task_id == t.id and d.status == MediaStatus.running:
        d.status = MediaStatus.failed
        d.error = msg
        db.commit()
