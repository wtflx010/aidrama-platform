"""孤儿视频任务恢复：找回"已提交 ComfyUI 但 worker 异常退出"的视频。

背景（2026-08-10）：generate_video 提交给 ComfyUI 后，若 worker 在轮询/
下载阶段崩溃（异常被 except 捕获标 failed），ComfyUI 上任务可能仍在生成甚至
已生成完毕，系统端却丢失了结果。generate_video 失败时若 provider_task_id
已落库，会派发本任务：轮询 ComfyUI 直到完成 → 下载落库，把 clip/task 恢复
为 succeeded，实现"报错流程与服务器自动对接"，无需人工干预。
"""
import logging
import os

from app.config import settings
from app.database import SessionLocal
from app.models.media import MediaStatus, VideoClip
from app.models.model_config import Model
from app.models.task import Task, TaskStatus
from app.providers.base import ProviderStatus, TaskHandle
from app.providers.registry import ProviderRegistry
from app.tasks.base import download_to_local, now
from app.tasks.celery_app import celery_app
from app.utils.media import apply_audio_fade_in

logger = logging.getLogger(__name__)

# 等待上限：ComfyUI 一个 5s 视频约 5~10 分钟，允许 24 次 × 60s = 24 分钟
_MAX_RETRIES = 24
_RETRY_SECONDS = 60


@celery_app.task(
    name="recover_orphan_video", bind=True,
    max_retries=_MAX_RETRIES, default_retry_delay=_RETRY_SECONDS,
)
def recover_orphan_video(self, task_id: str):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.status == TaskStatus.succeeded:
            return  # 已不存在或已恢复
        if not task.provider_task_id:
            logger.info("[recover] 任务 %s 未提交到 ComfyUI（provider_task_id 为空），无需恢复", task_id)
            return
        if task.target_type != "video":
            return  # 仅支持视频片段恢复
        clip = db.get(VideoClip, task.target_id)
        model = db.get(Model, task.model_id)
        if clip is None or model is None:
            return

        provider = ProviderRegistry.for_model(model)
        handle = TaskHandle(
            provider=provider.provider_type,
            providerTaskId=task.provider_task_id,
            pollUrl=task.poll_url
            or f"{provider._endpoint()}/history/{task.provider_task_id}",  # noqa: SLF001
            estimatedSeconds=0,
            meta={},
        )
        result = provider.getTaskResult(handle)

        if result.status == ProviderStatus.running:
            logger.info("[recover] ComfyUI 仍在生成 task=%s prompt=%s，%ss 后重试",
                        task_id, task.provider_task_id, _RETRY_SECONDS)
            raise self.retry(countdown=_RETRY_SECONDS)

        if result.status == ProviderStatus.succeeded and result.videoUrl:
            local_url = download_to_local(
                result.videoUrl, subdir=f"videos/{clip.id}",
                filename="clip.mp4", task_id=str(task_id),
            )
            # 音频淡入（与 generate_video 落库一致，消除 H3 开头瞬态爆音）
            _local = os.path.join(
                settings.media_dir, local_url.split("/static/media/", 1)[1]
            )
            apply_audio_fade_in(_local)
            clip.video_url = local_url
            clip.duration = result.duration or (
                clip.num_frames / clip.frame_rate if clip.frame_rate else 5.0
            )
            clip.status = MediaStatus.succeeded
            clip.error = None
            task.status = TaskStatus.succeeded
            task.error = None
            task.result_url = local_url
            task.progress = 100
            task.finished_at = now()
            db.commit()
            logger.info(
                "[recover] 视频已找回 clip_id=%s task=%s url=%s 时长=%.2fs",
                clip.id, task_id, local_url, clip.duration or 0,
            )
            return

        # ComfyUI 记录已丢失 / 任务真失败：保持 failed，不再折腾
        logger.warning(
            "[recover] 无法恢复 task=%s：%s",
            task_id, result.error or "ComfyUI 无输出（记录丢失或生成失败）",
        )
    except Exception as e:
        # self.retry 抛出的 Retry 异常属于正常重试计划，直接透传
        from celery.exceptions import Retry

        if isinstance(e, Retry):
            raise
        logger.warning("[recover] 恢复流程异常 task=%s: %s", task_id, e)
        raise
    finally:
        db.close()
