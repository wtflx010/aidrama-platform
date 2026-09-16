"""AI 视频草稿（Video Lab 页签）服务。

项目级独立视频生成：纯文生（T2V）/ 首尾帧（FL2VA）/ 参考视频+图片混合（R2V）。
- 参考素材（首尾帧/参考图/参考视频/资产）均可选，由 generate 按内容自动路由模型：
  有参考（图/视频/资产）→ R2V（video_kind=minimax_ref，ref2va 权重）
  仅首尾帧/纯文本 → FL2VA（video_kind=minimax，fl2va 权重）
- 文件上传走 base64（与资产上传一致，避免 python-multipart 依赖）
- enhance 走 LLM 优化（H3 Ref2VA 标签 + 负面词联动）
"""
import base64 as _b64
import json as _json
import logging
import os as _os
import re as _re
import uuid
from pathlib import Path as _Path

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.asset import Asset, AssetType
from app.models.media import MediaStatus
from app.models.model_config import Model, ModelType, ProviderType
from app.models.task import Task, TaskStatus, TaskType
from app.models.video_draft import VideoDraft

_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}
_DURATIONS = {5, 8, 10, 15}  # 5~15 秒（2026-08-23：移除 4s，对齐二采验证需求）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
# H3 R2V 节点硬上限：参考图 ≤9、参考视频 ≤3（nodes_minimax_h3.py 官方 schema）
_REF_IMAGE_MAX = 9
_REF_VIDEO_MAX = 3


def _validate(aspect_ratio: str, duration: int) -> None:
    if aspect_ratio not in _ASPECT_RATIOS:
        raise ValueError(f"不支持的画面比例: {aspect_ratio}（可选 16:9/9:16/1:1/4:3/3:4）")
    if duration not in _DURATIONS:
        raise ValueError(f"不支持的时长: {duration} 秒（可选 4/5/8/10/15）")


def create(db: Session, payload) -> VideoDraft:
    _validate(payload.aspect_ratio, payload.duration)
    draft = VideoDraft(
        project_id=payload.project_id,
        prompt=payload.prompt.strip(),
        negative_prompt=payload.negative_prompt,
        first_frame_url=payload.first_frame_url,
        last_frame_url=payload.last_frame_url,
        ref_image_urls=list(payload.ref_image_urls or []),
        ref_video_urls=list(payload.ref_video_urls or []),
        asset_refs=list(payload.asset_refs or []),
        aspect_ratio=payload.aspect_ratio,
        duration=payload.duration,
        resolution=_normalize_resolution(payload.resolution),
        status=MediaStatus.pending,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


_RESOLUTIONS = (None, "480p", "720p", "768p", "1080p")  # 1080p=超分二采产物档位


def _normalize_resolution(v) -> str | None:
    """规范化生成档位：480p/720p/768p 直通；None/空回退 None（跟随项目）。"""
    if not v:
        return None
    val = str(v).strip().lower()
    return val if val in _RESOLUTIONS else None


def update(db: Session, draft_id, payload) -> VideoDraft:
    draft = db.get(VideoDraft, draft_id)
    if not draft:
        raise ValueError("视频草稿不存在")
    data = payload.model_dump(exclude_unset=True)
    if data.get("aspect_ratio") is not None:
        _validate(data["aspect_ratio"], data.get("duration", draft.duration))
    if data.get("duration") is not None:
        _validate(data.get("aspect_ratio", draft.aspect_ratio), data["duration"])
    if data.get("resolution") is not None:
        data["resolution"] = _normalize_resolution(data["resolution"])
    for k, v in data.items():
        setattr(draft, k, v)
    db.commit()
    db.refresh(draft)
    return draft


def list_by_project(db: Session, project_id) -> list[VideoDraft]:
    return list(
        db.scalars(
            select(VideoDraft)
            .where(VideoDraft.project_id == project_id)
            .order_by(VideoDraft.created_at.desc())
        ).all()
    )


def list_all(db: Session) -> list[VideoDraft]:
    """全局草稿列表（AI 视频独立功能，不依赖项目）。"""
    return list(
        db.scalars(
            select(VideoDraft).order_by(VideoDraft.created_at.desc())
        ).all()
    )


def get(db: Session, draft_id) -> VideoDraft | None:
    return db.get(VideoDraft, draft_id)


def delete(db: Session, draft_id) -> bool:
    draft = db.get(VideoDraft, draft_id)
    if not draft:
        return False
    # 2026-08-25 用户反馈：删除正在生成的草稿时，ComfyUI 上的任务没有取消。
    # 先同步取消该草稿的生成/超分任务（标记 cancelled + 队列删除/中断远端 prompt），
    # 再删文件与行，避免 ComfyUI 继续白跑完整 prompt 浪费 GPU。
    _cancel_tasks_for(db, draft.id)
    # 删除草稿关联的本地素材文件（uploads 目录）
    _delete_local_files(draft)
    db.delete(draft)
    db.commit()
    return True


def _cancel_tasks_for(db: Session, draft_id) -> None:
    """删除草稿前，取消其关联的进行中（pending/running）生成与超分任务。

    复用 task_service.sync_cancel_provider：ComfyUI 侧按 /queue 精准处理——
    排队中 → 从队列删除；执行中 → /interrupt 中断。best-effort，不抛错，
    最终统一由 delete() 的 db.commit() 提交状态变更。
    """
    from datetime import datetime, timezone

    from app.services.task_service import sync_cancel_provider  # 函数内导入防循环

    tasks = (
        db.execute(
            select(Task).where(
                Task.target_type == "video_draft",
                Task.target_id == draft_id,
                Task.status.in_([TaskStatus.pending, TaskStatus.running]),
            )
        )
        .scalars()
        .all()
    )
    for t in tasks:
        t.status = TaskStatus.cancelled
        t.finished_at = datetime.now(timezone.utc)
        sync_cancel_provider(db, t)
    if tasks:
        logger.info("删除草稿 %s 时取消进行中任务 %s 个", draft_id, len(tasks))


def _delete_local_files(draft: VideoDraft) -> None:
    """删除草稿引用的本地上传文件（video_drafts/{project_id}/uploads/ 下的文件）。"""
    urls = [draft.first_frame_url, draft.last_frame_url]
    urls += list(draft.ref_image_urls or [])
    urls += list(draft.ref_video_urls or [])
    marker = "/static/media/video_drafts/"
    for u in urls:
        if not u or marker not in u:
            continue
        rel = u.split(marker, 1)[1]
        path = _Path(settings.media_dir) / "video_drafts" / rel
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def upload_file(db: Session, project_id, filename: str, data_base64: str) -> str:
    """保存上传的图片/视频到 media 目录，返回本地 URL。

    有项目 → {media_dir}/video_drafts/{project_id}/uploads/；
    无项目（AI 视频独立模式）→ {media_dir}/video_drafts/uploads/。
    """
    safe_name = _re.sub(r"[^\w.\-]", "_", filename or "upload.bin")
    ext = _Path(safe_name).suffix.lower()
    if ext not in _IMAGE_EXTS and ext not in _VIDEO_EXTS:
        raise ValueError(
            f"不支持的文件格式: {ext or '(无扩展名)'}"
            "（图片 png/jpg/jpeg/webp，视频 mp4/webm/mov/m4v）"
        )
    try:
        raw = _b64.b64decode(data_base64)
    except Exception:
        raise ValueError("base64 解码失败")
    if not raw:
        raise ValueError("文件数据为空")
    if len(raw) > 100 * 1024 * 1024:
        raise ValueError("文件过大（最大 100MB）")
    if project_id:
        sub = f"video_drafts/{project_id}/uploads"
    else:
        sub = "video_drafts/uploads"
    save_dir = _Path(settings.media_dir) / sub
    save_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"{_os.urandom(8).hex()}{ext}"
    (save_dir / unique_name).write_bytes(raw)
    return f"{settings.static_base_url}/media/{sub}/{unique_name}"


def resolve_asset_ref_urls(db: Session, asset_ids: list[str]) -> list[str]:
    """解析选中的资产 id → 参考图 URL（角色取四格图/四视图，场景/道具取封面）。

    与 resolve_r2v_refs 一致：角色优先 character_sheet_url（单张四格合一图，R2V 参考图
    规格），否则 four_view_urls（正面/侧面/背面/特写，脸部特写是锁脸核心视图）；
    场景优先 scene_sheet_url；道具取 cover_url。去重保序。
    """
    urls: list[str] = []
    for aid in asset_ids:
        try:
            asset = db.get(Asset, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            continue
        if not asset or not asset.cover_url:
            continue
        if asset.type == AssetType.character:
            sheet = asset.character_sheet_url
            if sheet:
                urls.append(sheet)
            else:
                views = [u for u in (asset.four_view_urls or []) if u]
                urls.extend(views[:4] if views else [asset.cover_url])
        elif asset.type == AssetType.scene:
            urls.append(asset.scene_sheet_url or asset.cover_url)
        else:
            urls.append(asset.cover_url)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def enhance(db: Session, body) -> tuple[str, str]:
    """LLM 优化提示词（H3 风格，含 <Picture N>/<Video N> 参考标签），联动负面词。

    不依赖草稿存在（优化发生在建草稿前），参考素材与参数全部来自请求体。
    返回 (enhanced_prompt, negative_prompt)。失败抛异常，由 API 层转 400。
    """
    ref_images: list[str] = []
    if body.first_frame_url:
        ref_images.append(body.first_frame_url)
    ref_images += list(body.ref_image_urls or [])
    ref_images += resolve_asset_ref_urls(db, list(body.asset_refs or []))
    ref_videos = list(body.ref_video_urls or [])

    ref_list_lines: list[str] = []
    for i, u in enumerate(ref_images[:_REF_IMAGE_MAX], start=1):
        ref_list_lines.append(f"  <Picture {i}>: {u}")
    for i, u in enumerate(ref_videos[:_REF_VIDEO_MAX], start=1):
        ref_list_lines.append(f"  <Video {i}>: {u}")
    ref_list = "\n".join(ref_list_lines) if ref_list_lines else "（无参考素材，纯文生视频）"

    prompt_text = _ENHANCE_PROMPT_TMPL.format(
        user_prompt=body.prompt.strip(),
        ref_list=ref_list,
        has_refs="有" if (ref_images or ref_videos) else "无",
        aspect_ratio=body.aspect_ratio or "16:9",
        duration=body.duration or 5,
    )

    from app.providers.registry import ProviderRegistry
    from app.services.prompt_enhance_service import _extract_json, _resolve_text_model

    model = _resolve_text_model(db, None, "script")
    provider = ProviderRegistry.for_model(model)
    messages = [
        {"role": "system", "content": "你是专业的 AI 视频提示词工程师，只输出符合要求的 JSON。"},
        {"role": "user", "content": prompt_text},
    ]
    last_err: Exception | None = None
    for attempt in range(1, 3):
        try:
            resp = provider.chat(messages)
            content = resp["choices"][0]["message"]["content"]
            data = _extract_json(content)
            prompt = (data.get("prompt") or "").strip()
            negative = (data.get("negative_prompt") or "").strip()
            if not prompt:
                raise ValueError("LLM 未返回 prompt")
            return prompt, negative or _FALLBACK_NEGATIVE
        except Exception as e:
            last_err = e
            if attempt < 2:
                import time
                time.sleep(2)
                continue
    raise last_err or RuntimeError("提示词增强失败")


_FALLBACK_NEGATIVE = (
    "low quality, lowres, blurry, watermark, text, subtitle, captions, logo, "
    "extra person, multiple people, duplicate, warped face, distorted face, "
    "deformed face, facial distortion, face morphing, melting face, "
    "flickering face, unstable face, disfigured face, cross-eyed, "
    "misplaced facial features, oversmoothed skin, plastic skin, "
    "stiff motion, morphing artifacts, flickering, jitter"
)

_ENHANCE_PROMPT_TMPL = """优化下面这段视频生成提示词，输出 JSON：{{"prompt": "...", "negative_prompt": "..."}}

【参考素材清单】（{ref_list}）

【用户需求】
{user_prompt}

【生成参数】
- 画面比例：{aspect_ratio}，时长：约 {duration} 秒（24fps）

【提示词编写要求】
1. 用简体中文编写（场景/动作/氛围/角色外貌用中文；景别/运镜可中英对照如「全景 wide shot」；风格关键词保留英文原文）。
2. 若【参考素材清单】有素材，必须显式用 <Picture N> / <Video N> 标签指代并声明职责，例如：
   - 「<Picture 1> 提供角色外观，人物的面部、发型、服装、体型必须与 <Picture 1> 完全一致，不得改动」
   - 「<Video 1> 提供动作与节奏参考，运动的连贯性、速度、幅度与 <Video 1> 一致」
   - 参考图未覆盖的画面区域可自由创作；标签编号必须与【参考素材清单】一一对应，禁止换序或合并。
   若清单为「无参考素材，纯文生视频」，则不要使用任何参考标签，直接写完整画面描述。
3. 写清镜头运动（推/拉/摇/移/跟及速度）、人物动作的时间顺序与力度重量感、环境与光照、材质质感。
4. 若画面存在声音场景（街道/雨/风/人群）或角色对白/旁白，追加声音描述引导模型生成原生音频；
   角色语音必须且只能用简体中文（zh-CN）说出，禁止中英混杂。
5. 结尾追加电影级质量词：highly detailed, sharp focus, shallow depth of field, cinematic lighting,
   subtle film grain, smooth 24fps motion, 8k uhd。
6. 禁止在画面中渲染字幕/文字气泡/水印。

【负面词要求】
negative_prompt 列出：低分辨率/模糊、水印字幕 logo、多余人物、面部崩坏（warped/deformed/distorted face）、
表情僵硬、运动不连贯（stiff motion, flickering, jitter）、塑料感皮肤（plastic skin）等。"""


def _resolve_video_model(db: Session, model_id, want_ref: bool) -> Model:
    """解析视频生成模型。

    want_ref=True → R2V（video_kind=minimax_ref，ref2va 权重）；
    want_ref=False → 默认模型（is_default=True 启用且 scene_codes 含 video），
    与主链路 _resolve_model 对齐——用户默认 LTX25-T2V 时纯文生即走该模型；
    无默认模型才回退 FL2VA（video_kind=minimax）。
    优先显式 model_id（须启用）> 默认模型 > 匹配 video_kind 的启用模型按 sort 升序。
    """
    if model_id:
        m = db.get(Model, model_id)
        if not m or not m.is_enabled:
            raise ValueError("模型不存在或已停用")
        return m
    # 2026-08-19 修复：纯文生（无参考）优先遵循「默认」视频模型（含 LTX25 系）。
    # 此前硬编码只匹配 video_kind=minimax，用户默认 LTX25-T2V 仍被 H3 顶替。
    if not want_ref:
        m = db.scalar(
            select(Model).where(
                Model.model_type == ModelType.video,
                Model.is_default.is_(True),
                Model.is_enabled.is_(True),
                Model.scene_codes.contains(["video"]),
            ).order_by(Model.sort.asc())
        )
        if m is not None:
            return m
    kind = "minimax_ref" if want_ref else "minimax"
    m = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.video,
            Model.is_enabled.is_(True),
            Model.capability["video_kind"].astext == kind,
        ).order_by(Model.sort.asc())
    )
    if m is None:
        m = db.scalar(
            select(Model).where(
                Model.model_type == ModelType.video,
                Model.is_enabled.is_(True),
                Model.scene_codes.contains(["video"]),
            ).order_by(Model.sort.asc())
        )
    if m is None:
        raise ValueError(f"未配置可用的视频模型（{kind}），请先在模型管理启用")
    return m


def generate(db: Session, draft_id, model_id=None) -> Task:
    """创建生成任务并派发 Celery。任务派发后草稿置 running。"""
    draft = db.get(VideoDraft, draft_id)
    if not draft:
        raise ValueError("视频草稿不存在")
    if draft.status == MediaStatus.running:
        raise ValueError("该草稿正在生成中，请等待完成")
    _validate(draft.aspect_ratio, draft.duration)
    ref_image_urls = list(draft.ref_image_urls or [])
    ref_video_urls = list(draft.ref_video_urls or [])
    asset_refs = list(draft.asset_refs or [])
    if len(ref_image_urls) > _REF_IMAGE_MAX:
        raise ValueError(f"参考图数量（{len(ref_image_urls)} 张）超过上限（{_REF_IMAGE_MAX} 张）")
    if len(ref_video_urls) > _REF_VIDEO_MAX:
        raise ValueError(f"参考视频数量（{len(ref_video_urls)} 个）超过上限（{_REF_VIDEO_MAX} 个）")
    # 路由：有参考（图/视频/资产）→ R2V；仅首尾帧/纯文本 → FL2VA
    want_ref = bool(ref_image_urls or ref_video_urls or asset_refs)
    model = _resolve_video_model(db, model_id, want_ref)

    task = Task(
        project_id=draft.project_id, type=TaskType.generate_video_draft,
        target_type="video_draft", target_id=draft.id, model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    draft.task_id = task.id
    draft.model_id = model.id
    draft.status = MediaStatus.running
    draft.error = None
    db.commit()
    db.refresh(task)

    from app.tasks.generate_video_draft import generate_video_draft_task

    generate_video_draft_task.apply_async(args=[str(task.id)], countdown=1)
    return task

def regenerate(db: Session, draft_id) -> tuple["VideoDraft", "Task"]:
    """超分（就地覆盖，2026-08-25 用户决策）：
    对已成功草稿的视频直接做「分块超分 + 帧数/时长/音轨保真」到 1080p；
    成功后**不产生新草稿行**——原视频文件删除、该草稿行就地升级为 1080p。
    （此前二采/超分是另建 1080p 行，用户明确要求覆盖当前视频。）
    """
    draft = db.get(VideoDraft, draft_id)
    if not draft:
        raise ValueError("视频草稿不存在")
    if draft.status != MediaStatus.succeeded or not draft.video_url:
        raise ValueError("草稿尚未生成成功，无法超分；请先完成该草稿视频")
    if draft.status == MediaStatus.running:
        raise ValueError("超分进行中，请等待完成")
    if (draft.resolution or "") == "1080p":
        raise ValueError("已是 1080p，无需再超分")
    model = db.scalar(
        select(Model)
        .where(Model.provider_type == ProviderType.comfyui, Model.is_enabled.is_(True))
        .order_by(Model.sort.asc())
        .limit(1)
    )
    if model is None:
        raise ValueError("未找到可用的 ComfyUI 模型（超分依赖 ComfyUI 服务器）")
    task = Task(
        project_id=draft.project_id,
        type=TaskType.upscale_video,
        target_type="video_draft",
        target_id=draft.id,  # 就地覆盖：任务目标即该草稿本身
        model_id=model.id,
        status=TaskStatus.pending,
        provider_task_id=_json.dumps({"tier": "4x", "overwrite": True}),  # 与分镜超分一致默认 4x
    )
    db.add(task)
    db.flush()
    draft.task_id = task.id
    db.commit()
    db.refresh(task)
    from app.tasks.upscale_video import upscale_video  # 延迟导入防循环

    upscale_video.delay(str(task.id))
    return draft, task

