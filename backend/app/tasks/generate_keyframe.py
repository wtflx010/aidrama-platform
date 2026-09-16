"""关键帧生成任务：有参考图走 imageToImage（一致性），无则 textToImage → 下载落库。

审核拦截重试：Agnes img2img 对正常内容存在间歇性误判
（content_policy_violation，四视图链路实测"同一 prompt 偶发拦截、重试可自愈"）。
对安全策略拦截做同 prompt 重试（最多 3 次），其他错误不重试以免掩盖真实问题。
"""
import logging
import time

from app.database import SessionLocal
from app.models.media import Keyframe, MediaStatus
from app.models.model_config import Model
from app.models.task import Task, TaskStatus
from app.providers.base import ImageOpts
from app.providers.errors import ProviderError, is_content_policy, map_to_chinese
from app.providers.registry import ProviderRegistry
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 审核拦截重试：最多 3 次（间隔 3s→6s），与四视图/资产链路一致
_POLICY_MAX_RETRIES = 3


def _is_content_policy(err: Exception) -> bool:
    """判断是否安全策略拦截（仅此类错误才自动重试）。"""
    return is_content_policy(err)


@celery_app.task(name="generate_keyframe", bind=True)
def generate_keyframe(self, task_id: str, ref_image_urls: list[str] | None = None, ref_labels: list[str] | None = None):
    db = SessionLocal()
    target_id = None  # 提前捕获，避免 rollback 后读过期 task 对象触发 ObjectDeletedError
    try:
        task = db.get(Task, task_id)
        if task is None:
            return  # 任务行已被级联删除，无需处理
        target_id = task.target_id
        kf = db.get(Keyframe, target_id)
        if kf is None:
            return  # 关键帧已被删除
        segment = kf.segment
        model = db.get(Model, task.model_id)
        provider = ProviderRegistry.for_model(model)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        kf.status = MediaStatus.running
        db.commit()

        # 2026-08-09 修复：任务重跑（retry_failed 批量重跑）只传 task_id，ref 参数
        # 丢失会退化为纯文生图（丢角色/场景/道具参考图，形象一致性锚点丢失）。
        # 未传 ref 时按分镜关联资产自动重建参考图（与 keyframe_service 正常生成一致）。
        if not ref_image_urls:
            try:
                from app.schemas.keyframe import KeyframeGenerate
                from app.services.keyframe_service import _resolve_refs_with_labels

                payload = KeyframeGenerate(use_reference=True)
                pairs = _resolve_refs_with_labels(db, kf.segment, payload)
                ref_image_urls = [u for u, _ in pairs] or None
                ref_labels = [l for _, l in pairs] or None
                if ref_image_urls:
                    logger.info(
                        "关键帧 %s 重跑重建参考图 %d 张（asset 自动解析）", kf.id, len(ref_image_urls)
                    )
            except Exception as e:  # noqa: BLE001 - 重建失败回退文生图，不阻断重跑
                logger.warning("关键帧 %s 重跑重建参考图失败，回退文生图: %s", kf.id, e)

        ratio = segment.episode.project.aspect_ratio
        # P4：取分镜增强提示词与负面词（缓存分镜级复用）。
        # 2026-08-10：keyframe_service 不再同步调 LLM（点击立即派发任务），
        # kf.prompt 为空（非自定义）时在此（worker 内）生成增强 prompt。
        from app.services.prompt_enhance_service import ensure_enhanced_prompt
        is_bilingual = bool((getattr(model, "capability", None) or {}).get("ref_engine"))
        if not (kf.prompt or "").strip():
            kf.prompt, negative_prompt = ensure_enhanced_prompt(
                db, segment, segment.episode.project, target="image", bilingual=is_bilingual,
                ref_labels=ref_labels,
            )
        else:
            _, negative_prompt = ensure_enhanced_prompt(
                db, segment, segment.episode.project, target="image", bilingual=is_bilingual,
                ref_labels=ref_labels,
            )
        opts = ImageOpts(ratio=ratio, size="2K", negative_prompt=negative_prompt)
        # Flux.2 Klein 多图参考指代：参考图语义标签（角色/场景/道具名）随任务传递，
        # provider 拼接 "Image N: <label>" 指代块，让模型知道每张参考图是什么
        if ref_labels:
            opts.reference_labels = ref_labels

        # 有参考图 → img2img（场景+角色+道具多图合成，保证一致性）；无 → 纯文生图
        # 审核拦截（content_policy_violation）为 Agnes 间歇性误判，同 prompt 重试可自愈
        for attempt in range(_POLICY_MAX_RETRIES):
            try:
                if ref_image_urls:
                    handle = provider.imageToImage(kf.prompt, ref_image_urls, opts)
                else:
                    handle = provider.textToImage(kf.prompt, opts)
                break
            except Exception as e:
                if _is_content_policy(e) and attempt < _POLICY_MAX_RETRIES - 1:
                    logger.warning(
                        "关键帧 %s 被安全策略拦截（第 %s/%s 次），重试中…", kf.id,
                        attempt + 1, _POLICY_MAX_RETRIES,
                    )
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
        update_task(
            db, task_id, provider=handle.provider,
            provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl, progress=10,
        )

        result = run_with_polling(db, task_id, provider, handle, poll_interval=2, timeout=180)
        local_url = download_to_local(
            result.imageUrls[0], subdir=f"keyframes/{kf.id}", filename="frame.png",
            task_id=task_id,
        )
        kf.image_url = local_url
        kf.status = MediaStatus.succeeded
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=local_url, finished_at=now(),
        )
        db.commit()
    except TaskCancelledError:
        # 用户取消/项目删除：不回写 failed，保持 cancelled（媒体已回退 pending）
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if target_id:
            kf = db.get(Keyframe, target_id)
            if kf:
                kf.status = MediaStatus.failed
                kf.error = msg
                db.commit()
    finally:
        db.close()
