"""幕级设计图 + 幕级视频生成任务（P7.7 两阶段拆分）。

阶段1（generate_episode_design）：只生成幕级设计图（幕首图/衔接图/幕尾图，N+1 张，
   img2img 资产参考 + LLM 增强 prompt），回填各行 first_frame_url/last_frame_url。
   任务结束不生成视频，用户确认首尾帧后单独触发阶段2。
阶段2（generate_episode_video）：只生成视频——校验每行首尾帧图已存在，
   逐行调 provider.imageToVideo（图片参考首尾帧 + 内容参考幕级叙事 prompt）。

任一行/图失败不影响其他（该行置 failed，幕 video_status=partial）。
"""
import logging
import os
import time

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.episode_video import EpisodeVideo
from app.models.media import MediaStatus
from app.models.model_config import Model, ModelType
from app.models.project import Episode
from app.models.segment import Segment
from app.models.task import Task, TaskStatus
from app.providers.base import ImageOpts, VideoOpts
from app.providers.errors import (
    ProviderError,
    is_content_policy,
    is_rate_limit,
    map_to_chinese,
)
from app.providers.registry import ProviderRegistry
from app.services.episode_video_service import (
    DEFAULT_PER_DURATION,
    _load_script,
    _resolve_design_refs,
    enhance_design_prompt,
    plan_design_images,
    plan_groups,
    resolve_r2v_refs,
)
from app.services.keyframe_service import _resolve_model
from app.services.style_service import get_effective_style_prompt, get_style_video_params
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 审核拦截重试：与关键帧链路一致（Agnes img2img 间歇性误判，同 prompt 重试可自愈）
_POLICY_MAX_RETRIES = 3
# 限流（429）重试：Agnes 并发下偶发限流，退避后重试可成功
_RATE_LIMIT_MAX_RETRIES = 3

# 幕级视频负面词：低质/文字字幕/多人/面部崩坏/AI 塑料感（对抗长视频崩坏）
_EPISODE_VIDEO_NEGATIVES = (
    "low quality, lowres, blurry, watermark, text, subtitle, captions, logo, "
    "extra person, multiple people, duplicate, warped face, distorted face, "
    "deformed face, facial distortion, face morphing, melting face, "
    "flickering face, unstable face, disfigured face, cross-eyed, "
    "misplaced facial features, oversmoothed skin, plastic skin, "
    "stiff motion, morphing artifacts, flickering, jitter"
)


def _load_task_rows(db, task_id: str) -> tuple[list[EpisodeVideo], str | None]:
    """按 task_id 查幕级视频行 + 首幕 id（per_duration 从首幕 script 读）。"""
    rows = list(
        db.scalars(
            select(EpisodeVideo)
            .where(EpisodeVideo.task_id == task_id)
            .order_by(EpisodeVideo.index.asc())
        ).all()
    )
    return rows, (rows[0].episode_id if rows else None)


def _collect_groups(db, rows) -> tuple[list[tuple[Episode, EpisodeVideo]], list[dict], int]:
    """每行对应一个幕：取该幕分镜分组（每幕 1 组），per_duration 从首幕 script 读。"""
    ep_rows: list[tuple[Episode, EpisodeVideo]] = []
    groups: list[dict] = []
    per_duration = DEFAULT_PER_DURATION
    for row in rows:
        row_ep = db.get(Episode, row.episode_id)
        if row_ep is None:
            continue
        script = _load_script(row_ep)
        if row.episode_id == rows[0].episode_id:
            per_duration = int(script.get("per_duration") or DEFAULT_PER_DURATION)
        segs = list(
            db.scalars(
                select(Segment).where(Segment.episode_id == row_ep.id).order_by(Segment.index)
            ).all()
        )
        g = plan_groups(segs, script, per_duration)
        if not g:
            continue
        ep_rows.append((row_ep, row))
        groups.append(g[0])
    return ep_rows, groups, per_duration


def _generate_design_image(
    db, task_id, episode_id: str, key: str, prompt: str, refs: list[str],
    ratio: str, negative: str,
) -> str:
    """生成一张幕级设计图（img2img 优先，无参考图回退文生图），返回本地 URL。

    审核拦截（content_policy_violation）为 Agnes 间歇性误判，同 prompt 重试。
    """
    model = _resolve_model(db, None, ModelType.image, "img2img" if refs else "keyframe")
    provider = ProviderRegistry.for_model(model)
    # 2026-08-07 修复：设计图从 4K 降回 2K。4K（5248×2944）超出 agnes-image
    # 稳定输出范围，细节崩坏、生成慢；2K 更快且质量更稳（视频阶段会再上采样）
    opts = ImageOpts(ratio=ratio, size="2K", negative_prompt=negative)
    handle = None
    attempt = 0
    rate_attempt = 0
    while True:
        try:
            handle = (
                provider.imageToImage(prompt, refs, opts) if refs
                else provider.textToImage(prompt, opts)
            )
            break
        except Exception as e:
            # 审核拦截：Agnes 间歇性误判，同 prompt 重试可自愈
            if is_content_policy(e) and attempt < _POLICY_MAX_RETRIES - 1:
                attempt += 1
                logger.warning(
                    "[episode_video] 设计图 %s 被安全策略拦截（第 %s/%s 次），重试中…",
                    key, attempt + 1, _POLICY_MAX_RETRIES,
                )
                time.sleep(3 * attempt)
                continue
            # 限流（429）：并发下偶发，退避后重试
            if is_rate_limit(e) and rate_attempt < _RATE_LIMIT_MAX_RETRIES - 1:
                rate_attempt += 1
                logger.warning(
                    "[episode_video] 设计图 %s 触发限流 429（第 %s/%s 次），%s 秒后重试…",
                    key, rate_attempt + 1, _RATE_LIMIT_MAX_RETRIES, 10 * rate_attempt,
                )
                time.sleep(10 * rate_attempt)
                continue
            raise
    result = run_with_polling(
        db, task_id, provider, handle,
        poll_interval=2, timeout=180,
    )
    if not result.imageUrls:
        raise ProviderError(f"设计图 {key} 完成但未返回图片 URL")
    local_url = download_to_local(
        result.imageUrls[0], subdir=f"episode_videos/{episode_id}/designs",
        filename=f"{key}.png", task_id=task_id,
    )
    return local_url


@celery_app.task(name="generate_episode_design", bind=True)
def generate_episode_design_task(self, task_id: str):
    """阶段1：只生成幕级设计图（幕首图/衔接图/幕尾图），回填各行首尾帧。

    完成后幕 video_status 保持 running（等待阶段2 视频）；设计图失败不影响其他。
    """
    db = SessionLocal()
    episode_id = None
    try:
        task = db.get(Task, task_id)
        if task is None:
            return  # 任务行已被级联删除
        episode_id = task.target_id
        rows, _ = _load_task_rows(db, task_id)
        if not rows:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="幕级视频行不存在", finished_at=now(),
            )
            return

        ep_rows, groups, per_duration = _collect_groups(db, rows)
        if not ep_rows:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="幕级设计图分组失败（无有效幕）", finished_at=now(),
            )
            return

        episode = db.get(Episode, episode_id)
        project = episode.project if episode else None
        ratio = project.aspect_ratio if project else "16:9"
        style = get_effective_style_prompt(db, project) if project else None

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)

        # ── 生成幕级设计图（N+1 张）→ 回填各行首尾帧 ──────────
        designs = plan_design_images(groups, {})
        total_steps = len(designs)
        design_urls: dict[str, str] = {}
        for di, d in enumerate(designs, start=1):
            # 任务被取消 → 停止后续
            cur = db.get(Task, task_id)
            if cur is not None and cur.status == TaskStatus.cancelled:
                raise TaskCancelledError("任务已被用户取消")
            try:
                # P7.4：LLM 增强设计图 prompt（画面描述 + 段剧情/景别/用途 + 资产设定 + 风格）
                prompt, img_negative = enhance_design_prompt(
                    db, d["scene_desc"], d["segment"], style,
                    narrative=d.get("narrative") or "",
                    shot_type=d.get("shot_type") or "",
                    role=d.get("role") or "",
                    core_only=bool(d.get("is_closing")),
                )
                refs = _resolve_design_refs(
                    db, d["segment"], core_only=bool(d.get("is_closing"))
                )
                url = _generate_design_image(
                    db, task_id, episode_id, d["key"], prompt, refs, ratio, img_negative,
                )
                design_urls[d["key"]] = url
                logger.info(
                    "[episode_video] 幕 %s 设计图 %s 生成成功（参考图 %d 张）",
                    episode_id, d["key"], len(refs),
                )
            except TaskCancelledError:
                raise  # 用户取消：终止整个任务，不当作单图失败吞掉
            except Exception as e:
                db.rollback()
                msg = map_to_chinese(e)
                logger.warning("[episode_video] 幕 %s 设计图 %s 生成失败：%s", episode_id, d["key"], msg)
                design_urls[d["key"]] = None
            update_task(db, task_id, progress=int(5 + di / total_steps * 90))

        # 回填各行首尾帧：行 i 首帧 = seg{i}_first；行 i 尾帧 = seg{i+1}_first（末行=segN_last）
        n = len(rows)
        all_failed = not any(v for v in design_urls.values())
        for i, row in enumerate(rows, start=1):
            first_url = design_urls.get(f"seg{i}_first")
            last_url = design_urls.get(f"seg{n}_last" if i == n else f"seg{i + 1}_first")
            row.first_frame_url = first_url
            row.last_frame_url = last_url
            # 首尾帧任一缺失 → 该行设计图失败（阶段2 将跳过）
            if not first_url or not last_url:
                row.status = MediaStatus.failed
                row.error = "幕首/幕尾图生成失败，请重新生成设计图"
        db.commit()

        if all_failed:
            # 2026-08-07 修复：全部设计图失败时任务必须标 failed（之前标 succeeded
            # 造成"任务成功但实际失败"的假象），并回滚幕 video_status
            ep = db.get(Episode, episode_id)
            if ep is not None:
                ep.video_status = "failed"
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="幕首/幕尾图全部生成失败，请重新生成设计图", finished_at=now(),
            )
            db.commit()
            return

        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            finished_at=now(),
        )
    except TaskCancelledError:
        # 用户取消：不回写 failed（幕 video_status 保留 running 由 task_service 处理）
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if episode_id:
            ep = db.get(Episode, episode_id)
            if ep is not None:
                ep.video_status = "failed"
                db.commit()
    finally:
        db.close()


@celery_app.task(name="generate_episode_video", bind=True)
def generate_episode_video_task(self, task_id: str):
    """阶段2：只生成幕级视频——每行首尾帧必须已存在（阶段1 产物），逐行出视频。"""
    db = SessionLocal()
    episode_id = None
    try:
        task = db.get(Task, task_id)
        if task is None:
            return  # 任务行已被级联删除
        episode_id = task.target_id
        rows, _ = _load_task_rows(db, task_id)
        if not rows:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="幕级视频行不存在", finished_at=now(),
            )
            return

        ep_rows, groups, per_duration = _collect_groups(db, rows)
        if not ep_rows:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error="幕级视频分组失败（无有效幕）", finished_at=now(),
            )
            return

        episode = db.get(Episode, episode_id)
        project = episode.project if episode else None
        model = db.get(Model, task.model_id)
        # 项目级视频分辨率（480p/720p）：覆盖 ComfyUI 视频模型档位，全项目统一
        provider = ProviderRegistry.for_model(
            model, resolution=(project.resolution if project else None)
        )
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)

        ratio = project.aspect_ratio if project else "16:9"
        style_params = get_style_video_params(db, project)
        video_negative = _EPISODE_VIDEO_NEGATIVES
        if style_params.get("negative_extra"):
            video_negative = f"{video_negative}, {style_params['negative_extra']}".strip(", ")
        # R2V 多参考：模型 video_kind=minimax_ref 时收集角色四视图作为多模态参考
        reference_assets = None
        if model is not None and (model.capability or {}).get("video_kind") == "minimax_ref":
            reference_assets = resolve_r2v_refs(
                db, project, ref_max=(model.capability or {}).get("ref_max", 8)
            )

        # ── 逐幕生成视频（每幕 1 个，首尾帧由阶段1 回填）──────────────
        succeeded = 0
        failed_rows: list[int] = []
        total = len(rows)
        for i, row in enumerate(rows, start=1):
            cur = db.get(Task, task_id)
            if cur is not None and cur.status == TaskStatus.cancelled:
                raise TaskCancelledError("任务已被用户取消")
            if row.status == MediaStatus.failed:
                # 阶段1 设计图失败的行：跳过视频生成，但计入失败集合，
                # 否则幕状态会被误判为 succeeded（此前只 continue，失败行未计入 failed_rows）
                failed_rows.append(row.index)
                continue
            row.status = MediaStatus.running
            db.commit()
            try:
                if not row.first_frame_url:
                    raise ProviderError("缺少幕首帧设计图，请先点「生成幕首/幕尾图」")
                opts = VideoOpts(
                    prompt=row.prompt or "",
                    width=row.width, height=row.height,
                    num_frames=row.num_frames, frame_rate=row.frame_rate,
                    duration=row.num_frames / row.frame_rate,
                    negative_prompt=video_negative,
                    shift_video=style_params.get("shift_video"),
                    # shift_audio 已从风格档剥离（2026-08-27）：None → capability 铁律（FL2V=6.0 / R2V=3.0）
                    shift_audio=None,
                    # 2026-08-07 音频修复：steps 由模型 capability 决定（turbo LoRA 统一 8 步，
                    # 音频在 8 步下才收敛，4 步撕裂；不传 style 的 26-32 步以免覆盖 turbo 档位）
                    steps=None,
                )
                handle = provider.imageToVideo(
                    row.first_frame_url, row.last_frame_url, opts,
                    reference_assets=reference_assets,
                )
                update_task(
                    db, task_id, provider=handle.provider,
                    provider_task_id=handle.providerTaskId,
                    poll_url=handle.pollUrl,
                    progress=int(40 + (i - 1) / total * 55),
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
                    result.videoUrl, subdir=f"episode_videos/{row.id}",
                    filename="clip.mp4", task_id=task_id,
                )
                # 2026-08-10：MiniMax H3 开头自带 ~0.1s 瞬态爆音 → 音频淡入消除
                # 注意：settings 用模块级导入（第 17 行），不能在函数内重复 import——
                # 否则 Python 视 settings 为局部变量，上方 run_with_polling 的
                # settings.celery_video_poll_interval 会报 UnboundLocalError。
                from app.utils.media import apply_audio_fade_in
                _local = os.path.join(settings.media_dir, local_url.split("/static/media/", 1)[1])
                apply_audio_fade_in(_local)
                row.video_url = local_url
                row.duration = result.duration or (row.num_frames / row.frame_rate)
                row.status = MediaStatus.succeeded
                row.error = None
                db.commit()
                succeeded += 1
                logger.info(
                    "[episode_video] 幕 %s 视频生成成功 url=%s 时长=%.2fs",
                    row.episode_id, local_url, row.duration or 0,
                )
            except TaskCancelledError:
                raise  # 用户取消：终止整个任务，不当作单幕失败吞掉
            except Exception as e:
                db.rollback()
                msg = map_to_chinese(e)
                row = db.get(EpisodeVideo, row.id)
                if row is not None:
                    row.status = MediaStatus.failed
                    row.error = msg
                    failed_rows.append(row.index)
                    db.commit()
                logger.warning("[episode_video] 幕 %s 视频生成失败：%s", row.episode_id if row else i, msg)

        # 各幕状态回填
        for ep_, _ in ep_rows:
            ep_ep = db.get(Episode, ep_.id)
            if ep_ep is not None:
                ep_ep.video_status = (
                    "succeeded" if succeeded and not failed_rows
                    else "partial" if succeeded
                    else "failed"
                )
        db.commit()

        if succeeded:
            update_task(
                db, task_id, status=TaskStatus.succeeded, progress=100,
                finished_at=now(),
            )
            if failed_rows:
                logger.warning(
                    "[episode_video] 幕级视频部分幕失败（%s），任务仍标记成功",
                    failed_rows,
                )
        else:
            update_task(
                db, task_id, status=TaskStatus.failed,
                error=f"幕级视频全部幕生成失败（{len(failed_rows)} 幕）",
                finished_at=now(),
            )
    except TaskCancelledError:
        # 用户取消：不回写 failed（幕 video_status 保留 running 由 task_service 处理）
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if episode_id:
            ep = db.get(Episode, episode_id)
            if ep is not None:
                ep.video_status = "failed"
                db.commit()
    finally:
        db.close()
