"""修片工作流任务（P1-4）：reframe 画幅重切 / voice-change 换声 / draw-to-video 局部重绘。

draw-to-video（H3 近似）：H3 ComfyUI 无原生「像素级局部重绘」节点，用 Ref2VA 参考链
近似——把 编辑后草图帧 作为参考图、源视频 作为参考视频，喂给 MiniMaxH3ReferenceToVideo
重生成：新片跟随原片内容、并按草图应用改动（整片重渲跟随，非逐像素只改局部）。
需模型 capability.draw_to_video 声明 + video_kind='minimax_ref'（ref_videos 生效）。
"""
import logging
import uuid

from app.config import settings
from app.database import SessionLocal
from app.models.task import Task, TaskStatus
from app.providers.base import VideoOpts
from app.providers.errors import ProviderError
from app.providers.registry import ProviderRegistry
from app.services import reframe_service
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_DRAW_RATIO_WH = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (720, 720)}
_DRAW_DEFAULT_RATIO = "16:9"
_DRAW_FRAMES = 121


@celery_app.task(name="reframe_video", bind=True)
def reframe_video(self, task_id: str, source_url: str, target_ratio: str = "9:16", resolution: str = "720p"):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=20)
        out_url = reframe_service.reframe(source_url, target_ratio, resolution)
        update_task(db, task_id, status=TaskStatus.succeeded, progress=100,
                    result_url=out_url, finished_at=now())
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=str(e)[:500], finished_at=now())
        db.commit()
        logger.exception("[rework] reframe 失败")
    finally:
        db.close()


@celery_app.task(name="voice_change_video", bind=True)
def voice_change_video(self, task_id: str, source_url: str, text: str,
                       voice_id: str | None = None, emotion: str | None = None):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=20)
        aud = reframe_service.synthesize_speech(db, text or "", voice_id, emotion)
        out_url = reframe_service.voice_change_video(source_url, aud)
        update_task(db, task_id, status=TaskStatus.succeeded, progress=100,
                    result_url=out_url, finished_at=now())
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=str(e)[:500], finished_at=now())
        db.commit()
        logger.exception("[rework] voice_change 失败")
    finally:
        db.close()


@celery_app.task(name="draw_to_video", bind=True)
def draw_to_video(self, task_id: str, source_url: str, prompt: str,
                  sketch_url: str | None = None, ratio: str = "16:9"):
    """草图/局部重绘出片（H3 Ref2VA 近似）：源视频 + 编辑草图帧 → 重生成。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=10)

        model, wf = reframe_service.find_draw_to_video_model(db)
        if model is None:
            raise ProviderError(
                "当前未配置支持 draw-to-video 的模型（需 capability.draw_to_video 声明）。"
                "请为视频模型声明 capability.draw_to_video，并将 video_kind 设为 minimax_ref。"
            )
        video_kind = str((model.capability or {}).get("video_kind", "minimax"))
        if video_kind != "minimax_ref":
            raise ProviderError(
                f"draw-to-video 走 H3 Ref2VA 参考链，需模型 video_kind='minimax_ref'"
                f"（当前 video_kind={video_kind}）。"
            )
        if not sketch_url:
            raise ProviderError("请提供编辑后的草图帧（sketch_url）")

        provider = ProviderRegistry.for_model(model)
        w, h = _DRAW_RATIO_WH.get(ratio, _DRAW_RATIO_WH[_DRAW_DEFAULT_RATIO])
        opts = VideoOpts(prompt=prompt or "", width=w, height=h, num_frames=_DRAW_FRAMES, frame_rate=24)
        # 草图帧作参考图(锚定构图/编辑意图) + 源视频作参考视频(跟随原片内容)
        handle = provider.imageToVideo(
            sketch_url, None, opts,
            reference_assets=[sketch_url],
            reference_videos=[source_url],
        )
        update_task(db, task_id, provider=handle.provider,
                    provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl, progress=15)

        result = run_with_polling(
            db, task_id, provider, handle,
            poll_interval=settings.celery_video_poll_interval,
            timeout=settings.celery_video_timeout,
        )
        if not result.videoUrl:
            raise ProviderError("draw-to-video 任务完成但未返回视频 URL")
        local_url = download_to_local(
            result.videoUrl, subdir=f"rework_draw/{uuid.uuid4().hex[:8]}",
            filename="draw.mp4", task_id=task_id,
        )
        update_task(db, task_id, status=TaskStatus.succeeded, progress=100,
                    result_url=local_url, finished_at=now())
        db.commit()
        logger.info("[rework] draw-to-video 完成 task=%s url=%s", task_id, local_url)
    except TaskCancelledError:
        db.rollback()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=str(e)[:500], finished_at=now())
        db.commit()
        logger.exception("[rework] draw_to_video 失败")
    finally:
        db.close()
