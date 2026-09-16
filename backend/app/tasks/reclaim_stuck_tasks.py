"""定时回收卡死的 running/pending 任务。

判定规则：
- running：last_heartbeat_at 或 updated_at 超过 STUCK_THRESHOLD 未刷新 → 探测远程
- pending：created_at 超过 PENDING_TIMEOUT 仍未被 worker 领取 → 标记 failed
  （worker 崩溃/未启动时任务一直 pending，需要回收）

2026-08-09（P2-1）：running 回收前先探测远程 provider（_probe_and_handle）——
任务在远程仍活跃时跳过回收，避免与存活的 worker 竞态。
2026-08-09（自动捡回）：探测到远程已 succeeded 且本地任务中断（服务重启等）时，
对 video / keyframe / asset_cover 三种链路自动下载产物落库并回写 succeeded，
不再标 failed——远程已生成的视频/图片不因本地 worker 重启而丢失。

由 Celery beat 每 60s 触发一次。独立 Session，不依赖外部入参。
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models.task import Task, TaskStatus, TaskType
from app.tasks.base import download_to_local, now, update_task
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

STUCK_THRESHOLD = timedelta(minutes=5)  # running 5min 无心跳 → 卡死
# 长耗时生成任务(R2V 视频/关键帧数分钟推理 + LLM 增强重试可能跨 5min):
# 提交前卡在 LLM/上传且无 provider_task_id 时放宽到 12min,避免预热/慢环节误回收
LONG_TASK_THRESHOLD = timedelta(minutes=12)
_LONG_TYPES = (
    TaskType.generate_video,
    TaskType.generate_keyframe,
    TaskType.generate_asset_cover,
    # 2026-08-25：超分链路 分块远程推理 + 本地保真后处理（concat/重编码），
    # 与 generate_video 同级放宽到 12 分钟，配合 upscale_video 的 heartbeat_guard 双保险。
    TaskType.upscale_video,
)
PENDING_TIMEOUT = timedelta(minutes=10)  # pending 10min 未领取 → 回收

# 探测结果三态
_PROBE_ACTIVE = "active"      # 远程仍活跃 → 跳过回收
_PROBE_RECOVERED = "recovered"  # 远程成功且本地已捡回 → 不回收
_PROBE_FAILED = "failed"      # 远程失败/不可达/无法捡回 → 回收标 failed


@celery_app.task(name="reclaim_stuck_tasks")
def reclaim_stuck_tasks():
    """扫描并回收卡死的 running/pending 任务。返回回收数量。"""
    db = SessionLocal()
    reclaimed = 0
    try:
        cutoff_running = now() - STUCK_THRESHOLD
        cutoff_pending = now() - PENDING_TIMEOUT

        # running 卡死：last_heartbeat_at 或 updated_at（取较新的）早于阈值
        stuck_running = db.scalars(
            select(Task).where(Task.status == TaskStatus.running)
        ).all()
        for t in stuck_running:
            hb = t.last_heartbeat_at or t.updated_at or t.started_at or t.created_at
            # 心跳时间可能是 naive datetime（旧数据），统一转 aware 比较
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            # 长任务(视频/关键帧)放宽回收阈值,给 LLM 增强+提交阶段余量
            cutoff_running = now() - (
                LONG_TASK_THRESHOLD if t.type in _LONG_TYPES else STUCK_THRESHOLD
            )
            if hb < cutoff_running:
                outcome = _probe_and_handle(db, t)
                if outcome == _PROBE_ACTIVE:
                    # 远程仍活跃（排队/执行中）→ 跳过本次回收，避免误标 failed
                    continue
                if outcome == _PROBE_RECOVERED:
                    # 远程成功且产物已捡回落库 → 任务/媒体均已回写 succeeded
                    reclaimed += 1
                    continue
                # 远程已失败/不可达/无法捡回 → 确认回收
                update_task(
                    db, str(t.id), status=TaskStatus.failed,
                    error=f"任务卡死（{STUCK_THRESHOLD.seconds // 60} 分钟无心跳，且远程任务已结束），已自动回收",
                    finished_at=now(),
                )
                # 同步回写子资源状态
                _mark_target_failed(db, t)
                reclaimed += 1

        # pending 超时：created_at 早于阈值
        stuck_pending = db.scalars(
            select(Task).where(
                Task.status == TaskStatus.pending,
                Task.created_at < cutoff_pending,
            )
        ).all()
        for t in stuck_pending:
            update_task(
                db, str(t.id), status=TaskStatus.failed,
                error=f"任务长时间未被领取（{PENDING_TIMEOUT.seconds // 60} 分钟），已自动回收",
                finished_at=now(),
            )
            _mark_target_failed(db, t)
            reclaimed += 1
    finally:
        db.close()
    return reclaimed


def _probe_and_handle(db, task: Task) -> str:
    """探测远程 provider 任务状态并处理卡死任务（三态返回）。

    2026-08-09：标记 failed 前先确认远程真实状态，避免与存活的 worker 竞态
    （worker 仅瞬时阻塞 → 心跳停 → 被误回收 → 完成后又覆盖 succeeded）。
    语义（与各 provider.getTaskResult 对齐）：
    - running（排队/运行中/网络抖动 raw.err 缺省）→ 远程仍活跃 → _PROBE_ACTIVE
    - running + raw.err（探测本身失败）→ 视为不可达 → _PROBE_FAILED
    - succeeded → 尝试自动捡回（_recover_remote_result）→ 成功 _PROBE_RECOVERED
      否则 _PROBE_FAILED（四视图/配音/幕级等复杂链路不捡回，回收可重跑）
    - failed / 抛异常 / 无 provider_task_id / 模型解析失败 → _PROBE_FAILED
    """
    # canvas/director 任务 provider_task_id 存画布配置 JSON（含远端 prompt_id），
    # 其它类型直接用该字段作为远端任务 id（2026-08-30 修复：否则探测/回收失效）
    remote_id = task.provider_task_id
    if task.type in (TaskType.canvas_generate, TaskType.director_generate):
        try:
            cfg = json.loads(task.provider_task_id or "{}")
            remote_id = (cfg or {}).get("prompt_id") or None
        except (ValueError, TypeError):
            remote_id = None
    if not remote_id:
        return _PROBE_FAILED  # 未提交到远程（提交前卡死）→ 直接回收
    try:
        from app.models.model_config import Model
        from app.providers.base import ProviderStatus, TaskHandle
        from app.providers.registry import ProviderRegistry

        model = db.get(Model, task.model_id) if task.model_id else None
        if not model:
            return _PROBE_FAILED
        provider = ProviderRegistry.for_model(model)
        handle = TaskHandle(
            provider=task.provider or provider.provider_type,
            providerTaskId=remote_id,
            pollUrl=task.poll_url,
        )
        result = provider.getTaskResult(handle)
        if result.status == ProviderStatus.running:
            raw = result.raw if isinstance(result.raw, dict) else {}
            if raw.get("err"):
                return _PROBE_FAILED  # 探测本身失败 → 视为不可达
            return _PROBE_ACTIVE      # 远程真的在跑 → 跳过
        if result.status == ProviderStatus.succeeded:
            return _PROBE_RECOVERED if _recover_remote_result(db, task, result) else _PROBE_FAILED
        return _PROBE_FAILED  # 远程失败
    except Exception as e:  # noqa: BLE001 - 探测失败按不可达处理（可重跑兜底）
        logger.warning("卡死任务 %s 远程探测失败，按不可达回收: %s", task.id, e)
        return _PROBE_FAILED


def _recover_remote_result(db, task: Task, result) -> bool:
    """远程已成功但本地任务中断（服务重启等）→ 捡回产物落库并回写 succeeded。

    2026-08-09（用户拍板）：避免远程已生成的视频/图片因本地 worker 重启而丢失。
    仅支持三种通用链路（产物单一、落库规则与任务内一致）：
      generate_video → VideoClip.video_url
      generate_keyframe → Keyframe.image_url
      generate_asset_cover → Asset.cover_url
    四视图（character_sheet_url/four_view_urls）、配音、幕级等涉及后处理或多产物，
    无法可靠重建 → 不捡回（走回收标 failed，用户重跑）。
    """
    from app.models.asset import Asset, MediaStatus
    from app.models.media import Keyframe, VideoClip

    try:
        if task.type == TaskType.generate_video and result.videoUrl:
            clip = db.get(VideoClip, task.target_id)
            if clip and not clip.video_url:
                local = download_to_local(
                    result.videoUrl, subdir=f"videos/{clip.id}", filename="clip.mp4",
                    task_id=str(task.id),
                )
                clip.video_url = local
                clip.duration = result.duration or (
                    clip.num_frames / clip.frame_rate if clip.frame_rate else None
                )
                clip.status = MediaStatus.succeeded
                update_task(
                    db, str(task.id), status=TaskStatus.succeeded, progress=100,
                    result_url=local, finished_at=now(),
                )
                db.commit()
                logger.info("[reclaim] 自动捡回视频 clip=%s url=%s", clip.id, local)
                return True
        elif task.type == TaskType.generate_keyframe and result.imageUrls:
            kf = db.get(Keyframe, task.target_id)
            if kf and not kf.image_url:
                local = download_to_local(
                    result.imageUrls[0], subdir=f"keyframes/{kf.id}", filename="frame.png",
                    task_id=str(task.id),
                )
                kf.image_url = local
                kf.status = MediaStatus.succeeded
                update_task(
                    db, str(task.id), status=TaskStatus.succeeded, progress=100,
                    result_url=local, finished_at=now(),
                )
                db.commit()
                logger.info("[reclaim] 自动捡回关键帧 kf=%s url=%s", kf.id, local)
                return True
        elif task.type == TaskType.generate_asset_cover and result.imageUrls:
            asset = db.get(Asset, task.target_id)
            if asset and not asset.cover_url:
                local = download_to_local(
                    result.imageUrls[0], subdir=f"assets/{asset.id}", filename="cover.png",
                    task_id=str(task.id),
                )
                asset.cover_url = local
                asset.status = MediaStatus.succeeded
                update_task(
                    db, str(task.id), status=TaskStatus.succeeded, progress=100,
                    result_url=local, finished_at=now(),
                )
                db.commit()
                logger.info("[reclaim] 自动捡回资产封面 asset=%s url=%s", asset.id, local)
                return True
    except Exception as e:  # noqa: BLE001 - 捡回失败回退回收（用户可重跑）
        logger.warning("[reclaim] 自动捡回失败 task=%s: %s", task.id, e)
        db.rollback()
    return False


def _mark_target_failed(db, task: Task):
    """回收任务时同步回写子资源状态为 failed，避免前端永久卡「生成中」。

    覆盖全部生成类目标（与 task_service._revert_media 的 target_type 取值对齐）：
    keyframe/video/voiceline/asset/bgm/sfx/novel/episode/action_sequence/video_draft。
    """
    try:
        from app.models.media import Keyframe, VideoClip, MediaStatus
        from app.models.asset import Asset
        from app.models.voice import VoiceLine
        from app.models.bgm import BgmTrack
        from app.models.sfx import SfxClip
        from app.models.novel import Novel, NovelAnalysisStatus
        from app.models.episode_video import EpisodeVideo
        from app.models.action_sequence import ActionSequence
        from app.models.video_draft import VideoDraft

        tt = task.target_type
        tid = task.target_id

        def _fail(obj, field="status", failed_value=MediaStatus.failed):
            if obj is not None and hasattr(obj, field):
                setattr(obj, field, failed_value)
                if hasattr(obj, "error"):
                    obj.error = "任务被回收（卡死/超时）"

        if tt == "keyframe":
            _fail(db.get(Keyframe, tid))
        elif tt == "video":
            _fail(db.get(VideoClip, tid))
        elif tt == "voiceline":
            _fail(db.get(VoiceLine, tid))
        elif tt in ("asset", "character", "scene", "prop"):
            _fail(db.get(Asset, tid))
        elif tt == "bgm":
            _fail(db.get(BgmTrack, tid), failed_value="failed")
        elif tt == "sfx":
            _fail(db.get(SfxClip, tid), failed_value="failed")
        elif tt == "novel":
            # 2026-08-27 修复：只有「剧本/小说本体」任务可回写剧本状态。
            # 剧本海报（generate_asset_cover 挂 target_type=novel）等旁路任务被回收时
            # 不得把已写好的剧本标 failed（此前海报 400 失败被回收 → 剧本被误标
            # 「任务被回收（卡死/超时）」），且已完成剧本不再降级。
            novel = db.get(Novel, tid)
            if novel is not None and novel.writing_status != "done":
                if task.type == TaskType.analyze_novel:
                    novel.analysis_status = NovelAnalysisStatus.failed
                    novel.error = "任务被回收（卡死/超时）"
                elif task.type in {
                    TaskType.write_novel,
                    TaskType.write_script,
                    TaskType.adapt_script,
                    TaskType.adapt_continuation,
                }:
                    novel.writing_status = "failed"
                    novel.error = "任务被回收（卡死/超时）"
        elif tt == "episode":
            # 幕级任务（拆幕产生多行）：按 task_id 回退全部 EpisodeVideo 行
            for ev in db.query(EpisodeVideo).filter(EpisodeVideo.task_id == task.id).all():
                _fail(ev)
        elif tt == "action_sequence":
            _fail(db.get(ActionSequence, tid))
        elif tt == "canvas_board":
            # 画布父任务被回收：把画布 document 里仍在 running/queued 的节点降级为 failed
            #（2026-08-30 修复：此前无该分支，节点永久卡「生成中」）
            from app.models.canvas_board import CanvasBoard

            board = db.get(CanvasBoard, tid)
            if board is not None and board.document:
                doc = dict(board.document)
                curated: list[dict] = []
                for n in (doc.get("nodes") or []):
                    d = dict(n.get("data") or {})
                    if d.get("status") in ("running", "queued"):
                        d["status"] = "failed"
                        d["error"] = "任务被回收（卡死/超时）"
                        d["progress"] = 100
                    curated.append({**n, "data": d})
                doc["nodes"] = curated
                board.document = doc
        elif tt == "video_draft":
            _fail(db.get(VideoDraft, tid))
        db.commit()
    except Exception:
        # 子资源回写失败不影响任务回收主流程
        db.rollback()
