"""视频业务服务。"""
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.media import Keyframe, MediaStatus, VideoClip
from app.models.model_config import ModelType
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.video import VideoGenerate
from app.services.keyframe_service import _resolve_model
from app.services.video_pipeline.model import resolve_shot_video_model
from app.utils.media import delete_media_file

logger = logging.getLogger(__name__)

# 分镜时长 → 视频帧数（8n+1 档位，24fps 下 121=5s、145=6s、169=7s、193=8s、217=9s、241=10s、
# 265=11s、289=12s、313=13s、337=14s、361=15s；≤441 兼容 ComfyUI LTX-2.5 / MiniMax H3）
# 由分镜 duration（LLM 按镜头内容自动配置，5~15s）精确换算
# （2026-08-16 起恢复「LLM 按剧本内容自动拆分分镜时长，最长 15 秒」，不再统一强制 5s）
_FRAME_OPTIONS = (121, 145, 169, 193, 217, 241, 265, 289, 313, 337, 361)  # 5s~15s 逐秒档位
# 中文朗读语速估算：约 5.5 字/秒（LLM 拆分台词分镜时按 duration×5.5 配置对白量）
_CHARS_PER_SEC = 5.5
# 兜底分镜时长（Segment.duration 缺失时）与最短时长（模型硬约束）
VIDEO_SECONDS = 5.0

# 项目视频分辨率 → (width, height)。2026-08-16：创建项目档案可选择 480P / 720P，
# 后续全部视频（关键帧图生视频/幕级/AI 视频页签）按项目分辨率档位执行；
# 尺寸与 provider 的 minimax_res 档位严格一致（32 倍数，H3 latent patch 对齐）。
# 2026-09-09 接入融合单文件模型后新增官方 0.5MP/0.7MP 档（H3 可生成，32 倍数对齐）；
# 720p/1080p 非 32 倍数（720p 由 provider 回退最近生成档，1080p 仅超分）。
_RESOLUTION_DIMS = {
    "0.1mp": {
        "16:9": (416, 224), "9:16": (224, 416),
        "4:3": (352, 256), "3:4": (256, 352), "1:1": (320, 320),
    },
    "0.2mp": {
        "16:9": (576, 320), "9:16": (320, 576),
        "4:3": (480, 384), "3:4": (384, 480), "1:1": (416, 416),
    },
    "0.25mp": {
        "16:9": (640, 352), "9:16": (352, 640),
        "4:3": (544, 416), "3:4": (416, 544), "1:1": (480, 480),
    },
    "0.3mp": {
        "16:9": (704, 384), "9:16": (384, 704),
        "4:3": (608, 448), "3:4": (448, 608), "1:1": (512, 512),
    },
    "0.4mp": {
        "16:9": (832, 480), "9:16": (480, 832),
        "4:3": (736, 544), "3:4": (544, 736), "1:1": (640, 640),
    },
    "0.6mp": {
        "16:9": (1088, 608), "9:16": (608, 1088),
        "4:3": (928, 704), "3:4": (704, 928), "1:1": (800, 800),
    },
    "0.8mp": {
        "16:9": (1216, 672), "9:16": (672, 1216),
        "4:3": (1056, 768), "3:4": (768, 1056), "1:1": (896, 896),
    },
    "0.9mp": {
        "16:9": (1280, 704), "9:16": (704, 1280),
        "4:3": (1088, 832), "3:4": (832, 1088), "1:1": (960, 960),
    },
    "1.0mp": {
        "16:9": (1344, 768), "9:16": (768, 1344),
        "4:3": (1184, 864), "3:4": (864, 1184), "1:1": (1024, 1024),
    },
    # 官方 16GB 低显存基准（960×544，8 步 turbo 原生训练尺寸）
    "0.5mp": {
        "16:9": (960, 544), "9:16": (544, 960),
        "4:3": (704, 528), "3:4": (528, 704), "1:1": (512, 512),
    },
    # 官方主推 recipe（0.7MP，1152×640）
    "0.7mp": {
        "16:9": (1152, 640), "9:16": (640, 1152),
        "4:3": (768, 576), "3:4": (576, 768), "1:1": (672, 672),
    },
    # 480P（对齐 MiniMax H3 480p 档：832×480）
    "480p": {
        "16:9": (832, 480), "9:16": (480, 832),
        "4:3": (640, 480), "3:4": (480, 640), "1:1": (480, 480),
    },
    # 720P（LTX-2.5 原生模板尺寸：1280×720；H3 非 32 倍数生成档，provider 会回退）
    "720p": {
        "16:9": (1280, 720), "9:16": (720, 1280),
        "4:3": (960, 720), "3:4": (720, 960), "1:1": (720, 720),
    },
    # 768P（MiniMax H3 训练基准分辨率；与 provider _MMAX_RES_GRADES 768p 档严格一致，32 倍数对齐）
    "768p": {
        "16:9": (1344, 768), "9:16": (768, 1344),
        "4:3": (1024, 768), "3:4": (768, 1024), "1:1": (768, 768),
    },
}

# 支持的视频分辨率（创建项目档案可选；官方 0.7mp 为融合模型主推档）
VIDEO_RESOLUTIONS = ("0.1mp", "0.2mp", "0.25mp", "0.3mp", "0.4mp", "0.5mp", "0.6mp", "0.7mp", "0.8mp", "0.9mp", "1.0mp", "480p", "720p", "768p")


def normalize_resolution(v: str | None) -> str:
    """规范化项目分辨率：已知档位直通；HD/None/未知 → 0.7mp（官方主推，旧数据兼容）。
    注意 720p 本身非 H3 生成档，provider 生成时会回退到最近可生成档。"""
    val = (v or "").strip().lower()
    return val if val in VIDEO_RESOLUTIONS else "0.7mp"


def dims_for_ratio(ratio: str | None, resolution: str | None = None) -> tuple[int, int]:
    """项目 (aspect_ratio, resolution) → (width, height)。未知/缺失回退 16:9 / 720p。"""
    res = normalize_resolution(resolution)
    return _RESOLUTION_DIMS[res].get(ratio or "", _RESOLUTION_DIMS[res]["16:9"])


def frames_for_duration(duration: float | None, frame_rate: int = 24) -> int:
    """分镜时长 → 视频帧数（8n+1 档位，9~441）。

    按 duration×fps 取最近的 8n+1 值：5s→121、10s→241、15s→361。
    注意：这是单镜链路的**估算值**，最终成片帧数由 provider 对齐到 MiniMax H3 的
    17n+5 网格（snap_h3_frames，见 app/constants.py）。两套网格并非冲突：
    估算用 8n+1 便于逐秒档位，成片前统一落到 H3 网格（见 test_video_frames 的一致性用例）。
    """
    fps = max(1, int(frame_rate or 24))
    target = max(0.2, float(duration or 5.0)) * fps
    n = round((target - 1) / 8)
    frames = n * 8 + 1
    return max(9, min(frames, 441))


def _resolve_shot_video_model(db: Session, payload: VideoGenerate, kf: Keyframe | None):
    """分镜视频模型路由（H3 唯一，2026-08-23 起）：生视频只使用 MiniMax H3。

    规则顺序：
    1. 显式指定 payload.model_id（须启用）→ 尊重用户选择（须为 H3 模型）；
    2. 无显式指定 → 统一优先多图参考（MiniMax H3 R2V, video_kind=minimax_ref），
       纯资产参考图直接出片（H3 链路，2026-08-10 用户拍板）；
    3. 无 R2V 可用 → 回落 H3 单图（minimax）。
    （2026-08-23 移除：LTX25 FLF2V 首尾帧链路及其模型已下线，生视频只用 H3）
    """
    # 收敛到 video_pipeline.model.resolve_shot_video_model（行为不变，逻辑单点）
    return resolve_shot_video_model(db, payload.model_id)


def _resolve_next_keyframe_url(db: Session, segment: Segment) -> str | None:
    """P6 首尾帧衔接：取本镜下一镜的最新成功关键帧作为本镜视频尾帧。

    首尾帧的两张图都是"设计好的静态图"（本镜关键帧=首帧、下一镜关键帧=尾帧），
    无串行依赖可并行生成；仅当下一镜已有成功关键帧时回填，否则 None 回退现状。
    """
    nxt = db.scalar(
        select(Segment).where(
            Segment.episode_id == segment.episode_id,
            Segment.index > segment.index,
        ).order_by(Segment.index.asc()).limit(1)
    )
    if nxt is None:
        return None
    nkf = db.scalar(
        select(Keyframe).where(
            Keyframe.segment_id == nxt.id,
            Keyframe.status == MediaStatus.succeeded,
            Keyframe.image_url.isnot(None),
        ).order_by(Keyframe.index.desc()).limit(1)
    )
    return nkf.image_url if nkf else None


def estimate_num_frames(segment: Segment, requested: int, frame_rate: int = 24) -> int:
    """视频帧数：以分镜 duration 为准（LLM 按镜头内容自动配置 5~15s → 121/241/361…帧）。

    2026-08-16 新规则（用户拍板「LLM 按剧本内容自动拆分分镜时长，最长 15 秒」）：
    duration 由 LLM 按镜头内容分配（5~15s，落库 clamp），此处按 duration×fps 精确换算
    8n+1 档位（5s→121、10s→241、15s→361）；LTX-2.5 / MiniMax H3 均支持该范围。
    requested 仅作参考（前端交互帧数），不再作为唯一依据。
    """
    dur = segment.duration if segment and segment.duration else None
    return frames_for_duration(dur, frame_rate)


# 中文对白行（行首 1~12 个中文字符 + 全/半角冒号），如「林浩：这单送完能赚不少」
# 对白由配音链路生成，不应进入画面提示词——MiniMax 等模型会把对白文本渲染成画面文字/口型/语音
_DIALOGUE_LINE_RE = re.compile(r"^\s*([\u4e00-\u9fff]{1,12})[：:]\s*.+$", re.MULTILINE)
# 非对白引导词黑名单：行首是这些词 + 冒号时视为画面描述而非对白（如「备注：xxx」）
_NON_DIALOG_PREFIXES = {
    "备注", "地址", "说明", "提示", "注意", "时间", "地点", "画面", "声音",
    "动作", "效果", "台词", "字幕", "镜头", "光线", "天气", "人物", "场景",
    "环境音", "风声", "雨声", "背景", "灯光", "色调",
}
# 句子内嵌说话片段：「林浩说：xxx」「他说“xxx”」「说：'xxx'」「说道：xxx」
# 说话/对白由配音链路（CosyVoice 中文配音）后期合成，视频提示词里写说话内容会被
# MiniMax 生成成角色语音（且倾向英文），与配音冲突。
_INLINE_SPEECH_RE = re.compile(
    r"[\u4e00-\u9fff]{0,12}(?:说道|说|道)(?:[：:]|(?=[\"'“”‘’]))"
    r"\s*[\"'“”‘’]?[^\"'“”‘’。！？；]*[\"'“”‘’]?\s*[。！？；]?[\"'“”‘’]?\s*"
)


def strip_dialogue_lines(text: str | None) -> str:
    """剔除提示词中的中文对白（独立行 + 内嵌说话片段），并清理残留标点与空行。"""
    if not text:
        return text or ""

    def _is_dialogue(m) -> str:
        return "" if m.group(1) not in _NON_DIALOG_PREFIXES else m.group(0)

    cleaned = _DIALOGUE_LINE_RE.sub(_is_dialogue, text)
    cleaned = _INLINE_SPEECH_RE.sub("", cleaned)
    # 清理剥离后残留的孤立标点（如「上，说：'xxx'」删后剩「上，」）
    cleaned = re.sub(r"\s*[,，:：]\s*$", "", cleaned, flags=re.MULTILINE)
    lines = [ln.rstrip() for ln in cleaned.splitlines() if ln.strip()]
    return "\n".join(lines)


def list_by_segment(db: Session, segment_id):
    return db.scalars(
        select(VideoClip).where(VideoClip.segment_id == segment_id).order_by(VideoClip.created_at.asc())
    ).all()


def _validate_generate(db: Session, segment_id, payload: VideoGenerate):
    """generate/regenerate 共用的无副作用前置校验（分镜/关键帧/模型可用）。

    2026-08-15（H2）：regenerate 必须先校验后删除旧视频，校验失败时
    保留用户已有成片，避免先删后验导致数据丢失。
    """
    segment = db.get(Segment, segment_id)
    if not segment:
        raise ValueError("分镜不存在")
    # 2026-08-09：keyframe_id 改为可选——无关键帧时直接走「资产参考图 + 提示词」
    # 链路（分镜级 R2V 直接出片，参考图在 generate_video 任务内按 segment 资产解析）。
    kf = None
    if payload.keyframe_id:
        kf = db.get(Keyframe, payload.keyframe_id)
        if not kf or kf.segment_id != segment_id:
            raise ValueError("关键帧不存在或不属于该分镜")
        if not kf.image_url:
            raise ValueError("关键帧尚未生成图片，无法作为视频首帧")
    # 2026-08-19：分镜视频模型路由——有关键帧→首尾帧(FLF2V)，无关键帧→多图参考(R2V)
    model = _resolve_shot_video_model(db, payload, kf)
    return segment, kf, model


def generate(db: Session, segment_id, payload: VideoGenerate):
    from app.tasks.generate_video import generate_video

    # 前置校验（与 regenerate 共用同一份逻辑，保证两者行为一致）
    segment, kf, model = _validate_generate(db, segment_id, payload)
    # 2026-08-23 修复「重新生成分镜后前端仍显示旧视频」：
    # 此前 generate（右栏「重新生成分镜」/ 画布节点均走此入口）只追加不删除，
    # 同分镜堆积多条成片，列表按 created_at ASC 返回、pickClip 取第一条 = 最旧，
    # ComfyUI 已生成新视频却永远不展示。与 regenerate/batch_videos 对齐：
    # 生成前先清掉该分镜旧视频（含磁盘文件+取消未完成任务），保证每分镜只留最新一条。
    deleted_old = delete_segment_clips(db, segment_id)
    if deleted_old:
        logger.info("[video] generate 前清理该分镜旧视频 %d 条（重新生成语义）segment_id=%s", deleted_old, segment_id)
    project_id = segment.episode.project_id
    project = segment.episode.project
    logger.info(
        "[video] generate 入口 segment_id=%s keyframe_id=%s 首帧=%s model=%s(%s) 参数=%sx%s %sfps frames=%s",
        segment_id, (kf.id if kf else None), (kf.image_url if kf else None), model.name, model.model_id,
        payload.width, payload.height, payload.frame_rate, payload.num_frames,
    )
    # P4：LLM 提示词增强（视频版，含角色/场景/道具/风格/动作时序/负面词，缓存分镜级复用）
    # 用户手动编辑过 prompt（≠分镜描述）时尊重自定义，不覆盖
    is_custom = bool(
        payload.prompt and payload.prompt.strip() != (segment.description or "").strip()
    )
    # 前端默认带出关键帧的完整提示词；若用户未编辑（提示词等于 kf.prompt），
    # 仍走 video 增强——避免把 image 版增强 prompt 直接当视频提示词（缺运镜/微动态/环境音）
    if is_custom and kf and payload.prompt.strip() == (kf.prompt or "").strip():
        is_custom = False
    # 2026-08-10：LLM 增强移入 worker（generate_video 任务内已有 ensure_enhanced_prompt，
    # 且带分镜级缓存）。此前 API 请求线程同步调 LLM（~10s），点击生成后迟迟不提交
    # ComfyUI；改为只落自定义 prompt，其余由 worker 增强，点击立即派发任务。
    enriched_prompt = payload.prompt if is_custom else None
    # 视频提示词不再剥离对白：P4 视频增强已把对白改写为「角色开口说话」的演绎描述
    # （模型原生语音生成对话/情绪），对白保留在提示词中引导模型说话
    # 视频帧数：以分镜 duration 为准（LLM 按镜头内容自动配置 5~15s → 121/241/361 帧）；
    # 台词超限由 LLM 拆分分镜/调时长保证，此处仅按分镜时长换算
    # 2026-08-23 分镜级参数优先（三列工作台右侧面板），其次项目级 video_params
    # （项目详情统一配置，批量生成即按项目级出片）：帧率 → frame_rate；
    # 视频大小/清晰度 → 宽高（均未设置时回退项目 aspect_ratio/resolution）
    gp = segment.gen_params or {}
    pgp = ((segment.episode.project.video_params or {}) if segment.episode and segment.episode.project else None) or {}

    def _pick(*keys):
        for k in keys:
            v = gp.get(k)
            if v not in (None, ""):
                return v
            v = pgp.get(k)
            if v not in (None, ""):
                return v
        return None

    try:
        shot_fps = int(_pick("fps") or payload.frame_rate or 24)
    except (TypeError, ValueError):
        shot_fps = int(payload.frame_rate or 24) or 24
    shot_fps = shot_fps if shot_fps > 0 else 24

    num_frames = estimate_num_frames(segment, payload.num_frames, shot_fps)
    if num_frames != (payload.num_frames or 0):
        logger.info(
            "[video] 帧数按分镜时长换算 %d → %d（%.1fs→%.1fs）segment_id=%s",
            payload.num_frames or 0, num_frames,
            (payload.num_frames or 0) / shot_fps, num_frames / shot_fps,
            segment_id,
        )
    # 尺寸：分镜 video_size/res 优先 → 项目 video_params → 项目 aspect_ratio/resolution；
    # 前端显式传宽高时尊重（2026-08-10 修复：之前固定默认 1280x720，项目选 9:16 时仍生成横屏视频）
    if payload.width is None or payload.height is None:
        ratio = str(_pick("video_size") or "").strip() or (project.aspect_ratio if project else None)
        resolution = str(_pick("res") or "").strip() or (project.resolution if project else None)
        width, height = dims_for_ratio(ratio, resolution)
    else:
        width, height = payload.width, payload.height
    clip = VideoClip(
        segment_id=segment_id, keyframe_id=(kf.id if kf else None), prompt=enriched_prompt,
        num_frames=num_frames, frame_rate=shot_fps,
        width=width, height=height,
        # 2026-08-09：无关键帧时不写首帧（走资产参考图链路，任务内解析）；
        # 有关键帧（旧链路/兼容）时仍以其为首帧
        first_frame_url=(kf.image_url if kf else None),
        # P6 首尾帧衔接：尾帧 = 下一镜关键帧（本镜关键帧=首帧），实现逐镜画面接续；
        # 无下一镜/下一镜未生成关键帧时 None，链路回退现状
        last_frame_url=_resolve_next_keyframe_url(db, segment),
        model_id=model.id, status=MediaStatus.pending,
    )
    db.add(clip)
    db.flush()

    task = Task(
        project_id=project_id, type=TaskType.generate_video,
        target_type="video", target_id=clip.id, model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    clip.task_id = task.id  # 回填，供前端按 media.task_id 轮询
    db.commit()
    db.refresh(clip)
    db.refresh(task)
    logger.info(
        "[video] 新建视频已落库并派发 clip_id=%s task_id=%s segment_id=%s prompt来源=%s prompt_len=%s",
        clip.id, task.id, segment_id, "自定义" if is_custom else "LLM增强", len(enriched_prompt or ""),
    )
    generate_video.delay(str(task.id))
    return clip, task


def regenerate(db: Session, video_id, payload: VideoGenerate):
    clip = db.get(VideoClip, video_id)
    if not clip:
        raise ValueError("视频不存在")
    segment_id = clip.segment_id
    logger.info(
        "[video] regenerate 入口 video_id=%s segment_id=%s 旧状态=%s 旧文件=%s",
        video_id, segment_id, clip.status, clip.video_url,
    )
    # H2 修复（2026-08-15）：删除旧视频前先用 generate 同一套校验预检，
    # 模型停用/关键帧缺失等失败时保留已有成片（原实现先删后验会丢数据）。
    _validate_generate(db, segment_id, payload)
    # 重新生成：删除该分镜全部旧视频（DB 行 + 磁盘文件 + 取消未完成任务），
    # 保证每分镜视频唯一（前端只展示最新一条，成片也按此合并）。
    deleted = delete_segment_clips(db, segment_id)
    db.commit()
    logger.info("[video] regenerate 已删除该分镜旧视频 %d 条，开始生成新视频", deleted)
    return generate(db, segment_id, payload)


def delete_segment_clips(db: Session, segment_id) -> int:
    """删除分镜下全部视频片段（DB 行 + 磁盘文件 + 取消任务），返回删除条数。

    供重新生成/批量重跑调用，保证每分镜只保留一条最新视频。
    """
    clips = db.scalars(select(VideoClip).where(VideoClip.segment_id == segment_id)).all()
    logger.info(
        "[video] delete_segment_clips segment_id=%s 待删除 %d 条旧视频",
        segment_id, len(clips),
    )
    for c in clips:
        _delete_clip(db, c)
    if clips:
        db.flush()
        logger.info("[video] delete_segment_clips 已删除 %d 条视频（DB 行 + 文件）", len(clips))
    return len(clips)


def delete(db: Session, video_id) -> bool:
    clip = db.get(VideoClip, video_id)
    if not clip:
        logger.warning("[video] delete 未找到视频 video_id=%s", video_id)
        return False
    logger.info(
        "[video] delete 入口 video_id=%s segment_id=%s status=%s video_url=%s",
        video_id, clip.segment_id, clip.status, clip.video_url,
    )
    _delete_clip(db, clip)
    db.commit()
    logger.info("[video] delete 完成 video_id=%s（DB 行 + 文件已删）", video_id)
    return True


def _cancel_related_task(db, task_id) -> None:
    """取消未完成的关联任务，避免 worker 处理已删除的媒体行。

    同时同步取消远程 provider（ComfyUI）上的生成任务，避免远端继续跑完浪费算力。
    """
    if not task_id:
        return
    t = db.get(Task, task_id)
    if t and t.status in (TaskStatus.pending, TaskStatus.running):
        from app.services.task_service import sync_cancel_provider

        sync_cancel_provider(db, t)
        old = t.status
        t.status = TaskStatus.cancelled
        t.error = "旧任务已取消（媒体被重新生成/删除）"
        logger.info(
            "[video] 取消未完成任务 task_id=%s 状态 %s -> %s",
            task_id, old, TaskStatus.cancelled,
        )
    elif t:
        logger.info("[video] 关联任务已是终态无需取消 task_id=%s status=%s", task_id, t.status)


def _delete_clip(db: Session, clip: VideoClip) -> None:
    """删除单个视频片段：取消任务 + 删除磁盘文件 + 删 DB 行（不 commit，由调用方提交）。"""
    logger.info(
        "[video] _delete_clip clip_id=%s segment_id=%s status=%s task_id=%s",
        clip.id, clip.segment_id, clip.status, clip.task_id,
    )
    _cancel_related_task(db, clip.task_id)
    delete_media_file(clip.video_url)
    db.delete(clip)