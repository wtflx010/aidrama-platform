"""任务业务服务。"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)


def get(db: Session, task_id) -> Task | None:
    return db.get(Task, task_id)


def list_by_project(db: Session, project_id, type: str | None = None, status: str | None = None):
    q = select(Task).where(Task.project_id == project_id)
    if type:
        q = q.where(Task.type == type)
    if status:
        q = q.where(Task.status == status)
    q = q.order_by(Task.created_at.desc())
    return db.scalars(q).all()


def resolve_segment_ref(db: Session, t: Task) -> dict | None:
    """分镜级任务的目标坐标（幕/镜序号 + 标题），供任务中心展示识别。

    生成视频任务（target_type=video → VideoClip.segment_id）、生成关键帧任务
    （target_type=keyframe → Keyframe.segment_id）按 分镜 → 幕 反查坐标；
    其他任务（资产/配音/幕级等）返回 None，前端照旧展示目标 id。
    """
    seg = None
    if t.target_type == "video":
        from app.models.media import VideoClip
        from app.models.segment import Segment

        clip = db.get(VideoClip, t.target_id)
        if clip is not None:
            seg = db.get(Segment, clip.segment_id)
    elif t.target_type == "keyframe":
        from app.models.media import Keyframe
        from app.models.segment import Segment

        kf = db.get(Keyframe, t.target_id)
        if kf is not None:
            seg = db.get(Segment, kf.segment_id)
    if seg is None:
        return None
    from app.models.project import Episode

    ep = db.get(Episode, seg.episode_id) if seg.episode_id else None
    # 注意：分镜序号与工作台展示一致——ep.index 从 0 起（展示 +1 成「幕1」），
    # 而 seg.index 从 1 起（展示直接用它，不再 +1）。此前误 +1 导致任务中心
    # 比分镜工作台多一位（把第一镜 1-1 显示了 1-2）。
    return {
        "episode_index": (ep.index + 1) if ep and ep.index is not None else None,
        "segment_index": seg.index if seg.index is not None else None,
        "title": seg.title,
        "description": (seg.description or "")[:40],
    }


def _revert_media(db: Session, t: Task) -> None:
    """任务取消后回退关联媒体状态为 pending，避免前端卡在「生成中」无法重新执行。

    此前仅资产生成类回退，关键帧/视频/配音等取消后媒体 status 仍卡 running，
    ShotEditor 等面板按 media.status 判断禁用生成按钮 → 取消后无法再次执行。
    target_type 取值（见各服务创建 Task 处）：
      character/scene/prop/asset → Asset；keyframe → Keyframe；video → VideoClip；
      voiceline → VoiceLine；bgm → BgmTrack；sfx → SfxClip；novel → Novel；
      episode → EpisodeVideo（target_id 是 episode_id，仅幕级视频任务）。
    """
    from app.models.asset import Asset, MediaStatus
    from app.models.bgm import BgmTrack
    from app.models.episode_video import EpisodeVideo
    from app.models.media import Keyframe, VideoClip
    from app.models.novel import Novel, NovelAnalysisStatus
    from app.models.sfx import SfxClip
    from app.models.task import TaskType
    from app.models.voice import VoiceLine

    def _reset_status(obj) -> None:
        if obj is not None and getattr(obj, "status", None) == MediaStatus.running:
            obj.status = MediaStatus.pending

    def _reset_str_status(obj, field: str = "status", from_values=("running",)) -> None:
        if obj is not None and getattr(obj, field, None) in from_values:
            setattr(obj, field, "pending")

    tt = t.target_type
    if tt in ("character", "scene", "prop", "asset"):
        _reset_status(db.get(Asset, t.target_id))
    elif tt == "keyframe":
        _reset_status(db.get(Keyframe, t.target_id))
    elif tt == "video":
        clip = db.get(VideoClip, t.target_id)
        if t.type == TaskType.upscale_video:
            # 超分：取消后直接作废 clip。保留 pending 会永久卡「排队中」，且
            # has_upscaled() 把「非 failed 的超清 clip」视为已存在 → 挡住重跑
            # （2026-08-23 批量取消遗留 18 个 zombie 的根治）。
            if clip is not None and clip.status in (MediaStatus.running, MediaStatus.pending):
                clip.status = MediaStatus.failed
                clip.error = "任务已取消，超分未完成"
        else:
            _reset_status(clip)
    elif tt == "voiceline":
        _reset_status(db.get(VoiceLine, t.target_id))
    elif tt == "bgm":
        _reset_str_status(db.get(BgmTrack, t.target_id))
    elif tt == "sfx":
        _reset_str_status(db.get(SfxClip, t.target_id))
    elif tt == "novel":
        # Novel 运行态是 "analyzing"（非 "running"）：仅 analyze 任务会置位；
        # 取消后回退 pending，否则 trigger_analyze 的「正在分析中」400 会阻止再次执行。
        if t.type == TaskType.analyze_novel:
            _reset_str_status(
                db.get(Novel, t.target_id), field="analysis_status",
                from_values=(NovelAnalysisStatus.analyzing,),
            )
    elif tt == "episode" and t.type in (
        TaskType.generate_episode_design,
        TaskType.generate_episode_video,
    ):
        # 2026-08-09 修复（M2）：拆幕产生的新幕 EpisodeVideo 行通过 task_id 关联，
        # 取消时须按 task_id 回退全部行（旧实现按 episode_id == target_id 只回退首幕，
        # 新幕行残留 running → 前端卡片永久「生成中」）
        for ev in db.query(EpisodeVideo).filter(EpisodeVideo.task_id == t.id).all():
            _reset_status(ev)
    elif tt == "action_sequence":
        # 2026-08-10 白模故事版：动作序列取消后回退 pending，前端看板可重新触发
        from app.models.action_sequence import ActionSequence

        _reset_status(db.get(ActionSequence, t.target_id))
    elif tt == "video_draft":
        # 2026-08-11 AI 视频页签：取消后回退草稿 pending，可重新生成
        from app.models.video_draft import VideoDraft

        _reset_status(db.get(VideoDraft, t.target_id))


def sync_cancel_provider(db: Session, t: Task | None) -> None:
    """同步取消远程 provider 上的生成任务（best-effort，不抛错）。

    ComfyUI 等远程生成服务在系统标记 cancelled 后仍会继续跑完（浪费算力且
    worker 轮询期间可能回写覆盖状态），须主动调用 provider.cancel 让远端停下。
    仅对已提交的任务（provider_task_id 存在）执行；无法解析模型/Provider 时静默跳过。
    """
    if t is None or not t.provider_task_id:
        return
    # canvas/director 任务的 provider_task_id 是画布配置 JSON（含远端 prompt_id），
    # 其它类型直接用该字段作为远端任务 id（2026-08-30 修复：否则 cancel 匹配不到队列）
    remote_id = t.provider_task_id
    if t.type in (TaskType.canvas_generate, TaskType.director_generate, TaskType.project_director):
        try:
            cfg = json.loads(t.provider_task_id or "{}")
            remote_id = (cfg or {}).get("prompt_id") or None
        except (ValueError, TypeError):
            remote_id = None
    if not remote_id:
        return
    try:
        from app.models.model_config import Model
        from app.providers.registry import ProviderRegistry

        model = db.get(Model, t.model_id) if t.model_id else None
        if not model:
            return
        provider = ProviderRegistry.for_model(model)
        provider.cancel(remote_id)
    except Exception as e:  # noqa: BLE001 - 取消是 best-effort，失败不影响主流程
        logger.warning("同步取消远程任务失败 task_id=%s: %s", t.id, e)


def cancel(db: Session, task_id) -> Task | None:
    t = db.get(Task, task_id)
    if not t:
        return None
    # best-effort：标记取消。P0 不强求 worker 立即停止（worker 完成可能回写覆盖）。
    if t.status not in (TaskStatus.succeeded, TaskStatus.failed, TaskStatus.cancelled):
        t.status = TaskStatus.cancelled
        t.finished_at = datetime.now(timezone.utc)
        # 同步取消远程 provider 任务（ComfyUI 队列删除 + 中断），避免远端继续跑
        sync_cancel_provider(db, t)
        # 回退关联媒体状态为 pending：取消后媒体可重新执行，前端不再误显示「生成中」
        _revert_media(db, t)
        db.commit()
        db.refresh(t)
    return t


def retry_failed(db: Session, project_id, type: str | None = None) -> tuple[list[Task], str]:
    """批量重跑失败任务：重置失败任务为 pending 并重新派发对应 celery 任务。

    2026-08-09（用户需求）：任务中心一键重跑失败项。只处理 status=failed 的任务；
    可选按任务类型过滤（type=generate_video 等）。任务名与 TaskType 值一致
    （celery_app include 注册，如 generate_video / generate_keyframe），
    直接 send_task(t.type.value) 派发。目标媒体（clip/keyframe/asset）状态由
    任务内部重新置 running，无需在此恢复。
    """
    from app.tasks.celery_app import celery_app

    q = select(Task).where(Task.project_id == project_id, Task.status == TaskStatus.failed)
    if type:
        q = q.where(Task.type == type)
    tasks = list(db.scalars(q.order_by(Task.created_at.desc())).all())
    if not tasks:
        return [], "无失败任务可重跑"

    # 先在重置前备份 payload 型任务的持久化配置（provider_task_id 字段被两类任务复用：
    #   - export_film：导出选项 JSON（export_service.export 持久化，任务端不读该字段）
    #   - canvas_generate：画布批量生成配置 JSON（canvas_service.generate 持久化，任务端从该字段读取）
    # 其它类型存的是远端 provider 任务 id，重跑时必须清空以免误用旧 id。
    saved_payloads: dict[str, dict] = {}
    for t in tasks:
        if t.type in (TaskType.export_film, TaskType.canvas_generate, TaskType.director_generate):
            try:
                cfg = json.loads(t.provider_task_id or "{}")
                if isinstance(cfg, dict) and cfg:
                    saved_payloads[str(t.id)] = cfg
            except (ValueError, TypeError):
                logger.warning("重跑任务 payload 解析失败 task_id=%s type=%s", t.id, t.type)

    retried: list[Task] = []
    for t in tasks:
        t.status = TaskStatus.pending
        t.error = None
        t.progress = 0
        # canvas_generate / director_generate 的 provider_task_id 存画布节点配置，任务端依赖它恢复
        # 生成目标；清空会导致重跑 100%「配置解析失败」（2026-08-15 修复 / 2026-08-30 补 director）。
        if t.type not in (TaskType.canvas_generate, TaskType.director_generate):
            t.provider_task_id = None
        t.poll_url = None
        t.started_at = None
        t.finished_at = None
        t.last_heartbeat_at = None
        retried.append(t)
    db.commit()

    dispatched, failed_dispatch = 0, 0
    for t in retried:
        try:
            # 2026-08-09 修复：generate_bgm / generate_sfx 任务签名要求第二个必填
            # 参数 project_id（原 API 派发时显式传入），统一只传 task_id 会触发
            # TypeError 导致重跑无效。从 Task.project_id 补参，其余类型均为单参。
            args: list = [str(t.id)]
            if t.type in (TaskType.generate_bgm, TaskType.generate_sfx):
                args.append(str(t.project_id) if t.project_id else "")
            # 2026-08-09（P2-2）：导出任务恢复首次导出的选项参数（持久化在
            # provider_task_id JSON 中，见 export_service.export），重跑与首次一致。
            # 注意：必须在重置 provider_task_id 之前读取（旧实现先清后读恒为空）。
            kwargs: dict = {}
            if t.type == TaskType.export_film:
                kwargs = {
                    k: bool(v) for k, v in (saved_payloads.get(str(t.id)) or {}).items()
                }
            celery_app.send_task(t.type.value, args=args, kwargs=kwargs)
            dispatched += 1
        except Exception as e:  # noqa: BLE001 - 单任务派发失败不阻断其余
            failed_dispatch += 1
            logger.warning("重跑任务派发失败 task_id=%s type=%s: %s", t.id, t.type, e)
    msg = f"已重跑 {dispatched} 个失败任务" + (f"，{failed_dispatch} 个派发失败" if failed_dispatch else "")
    return retried, msg
