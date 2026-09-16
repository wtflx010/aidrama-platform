"""视频超分业务服务：基于成功的 480p 原片创建「超高清版」clip + 超分任务。

设计要点（2026-08-23，按用户反馈的"光晕过锐 + 帧率/时长漂移 + 末帧异常"定死）：
- 超分产物是**新的 VideoClip 行**（is_upscaled=True, upscale_of_id=原片），保留 480p 母片，
  播放/导出前端与后端都优先取超清版（is_upscaled desc）。
- 档位（tier）透传：4x（4x-UltraSharp，带抑晕）默认 / 2x（RealESRGAN_x2plus 快速档，2026-08-23 起两档均带抑晕且输出归一 1080p）；
  存进 Task.provider_task_id 的 JSON 配置（worker 读取，同时天然标记超分任务的源配置）。
- 规格保真与抗伪影在 worker / 模板层强制（本地帧数校验 + 原音轨混回 + 模板抑晕）。
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import MediaStatus, VideoClip
from app.models.model_config import Model, ModelType, ProviderType
from app.models.task import Task, TaskStatus, TaskType

logger = logging.getLogger(__name__)


def target_dims(src_w: int, src_h: int, tier: str = "4x"):
    """超分目标尺寸（偶数对齐）。

    - tier="4x"：4× 超分后归一到 1920×1080 画布（9:16 → 1080×1920）——超采样后再缩放，
      得到标准 1080p 清晰度。
    - tier="2x"：2× 超分后同样归一到标准 1080p（2026-08-23 起；此前保持 2× 原生
      1664×960，与 4x 档画质标识不一致）。
    """
    if not src_w or not src_h:
        return (1920, 1080)
    # 2026-08-23：2x 档同样归一到标准 1080p（此前保持 2× 原生 1664×960）——
    # 与 4x 档一致输出 1080p 级产物；速度差异仅来自更快的 RealESRGAN_x2plus 模型。
    if src_w >= src_h:
        scale = min(1920 / src_w, 1080 / src_h)
        w = max(2, round(src_w * scale))
        h = max(2, round(src_h * scale))
    else:
        scale = min(1080 / src_w, 1920 / src_h)
        w = max(2, round(src_w * scale))
        h = max(2, round(src_h * scale))
    return (w + (w % 2), h + (h % 2))


def _resolve_upscale_model(db: Session) -> Model:
    """取用于超分的 ComfyUI 模型（超分在 ComfyUI 上执行，模型用于 provider 路由与记账）。

    2026-09-17 收紧：不再「任意取第一个 ComfyUI 模型」。超分对象是成片视频 clip，
    故优先取启用的 ComfyUI **视频**模型；无视频模型再回退任意启用 ComfyUI 模型；
    若连一个可用 ComfyUI 模型都没有则明确报错。
    """
    rows = db.scalars(
        select(Model)
        .where(Model.provider_type == ProviderType.comfyui, Model.is_enabled.is_(True))
        .order_by((Model.model_type == ModelType.video).desc(), Model.sort.asc())
    ).all()
    if not rows:
        raise ValueError("未找到可用的 ComfyUI 模型（超分依赖 ComfyUI 服务器）")
    return rows[0]


def find_source_clip(db: Session, segment_id):
    """该分镜可超分的最新媒体：成功且未超分（is_upscaled=false）的视频。"""
    return db.scalar(
        select(VideoClip)
        .where(
            VideoClip.segment_id == segment_id,
            VideoClip.status == MediaStatus.succeeded,
            VideoClip.video_url.is_not(None),
            VideoClip.is_upscaled.is_(False),
        )
        .order_by(VideoClip.created_at.desc())
    )


def has_upscaled(db: Session, source_clip_id) -> bool:
    """该原片是否已有超分（未失败的即视为已存在，避免重复派发）。"""
    return (
        db.scalar(
            select(VideoClip.id).where(
                VideoClip.upscale_of_id == source_clip_id,
                VideoClip.status != MediaStatus.failed,
            )
        )
        is not None
    )


def generate(db: Session, source_clip: VideoClip, tier: str = "4x"):
    """为已成功的原片创建超清版 clip + 派发 upscale_video 任务。"""
    from app.tasks.upscale_video import upscale_video  # 延迟导入防循环

    if source_clip.status != MediaStatus.succeeded or not source_clip.video_url:
        raise ValueError("原片未生成成功，无法超分")
    seg = source_clip.segment
    if seg is None:
        raise ValueError("原片所属分镜不存在")
    project_id = seg.episode.project_id if seg.episode else None
    if project_id is None:
        raise ValueError("原片所属项目不存在")
    model = _resolve_upscale_model(db)
    tier = str(tier or "4x").lower()
    if tier not in ("4x", "2x"):
        raise ValueError("超分档位仅支持 4x / 2x")
    w, h = target_dims(source_clip.width, source_clip.height, tier)
    clip = VideoClip(
        segment_id=source_clip.segment_id,
        num_frames=source_clip.num_frames,
        frame_rate=source_clip.frame_rate,
        width=w, height=h,
        duration=source_clip.duration,
        model_id=model.id,
        status=MediaStatus.pending,
        is_upscaled=True,
        upscale_of_id=source_clip.id,
    )
    db.add(clip)
    db.flush()
    task = Task(
        project_id=project_id, type=TaskType.upscale_video,
        target_type="video", target_id=clip.id, model_id=model.id,
        status=TaskStatus.pending,
        # 任务配置（档位）存入 provider_task_id JSON；worker 读取。超分不依赖单一 prompt id
        provider_task_id=json.dumps({"tier": tier}),
    )
    db.add(task)
    db.flush()
    clip.task_id = task.id
    db.commit()
    db.refresh(clip)
    db.refresh(task)
    logger.info("[upscale] 已创建超分任务 clip_id=%s src_id=%s tier=%s target=%sx%s task_id=%s",
                clip.id, source_clip.id, tier, w, h, task.id)
    upscale_video.delay(str(task.id))
    return clip, task