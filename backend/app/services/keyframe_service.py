"""关键帧业务服务。"""
import logging
import os
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetType
from app.models.media import Keyframe, MediaStatus
from app.models.model_config import Model, ModelType
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.keyframe import KeyframeGenerate
from app.utils.media import delete_media_file

logger = logging.getLogger(__name__)

# 景别 → 四视图索引（four_view_urls=[正面, 侧面, 背面, 特写]：
#   特写/近景→特写[3]（脸部细节最清晰），中景/全景/远景→正面[0]（正面即全身））
_SHOT_TYPE_VIEW_INDEX = {
    "特写": 3,  # 面部特写（脸部细节最清晰）
    "近景": 3,  # 面部特写
    "中景": 0,  # 正面（全身）
    "全景": 0,  # 正面（全身）
    "远景": 0,  # 正面（全身，环境人）
}
# 运镜 → 视图索引（覆盖景别）
_CAMERA_VIEW_INDEX = {
    "摇": 1,  # 侧面（摇镜头常见侧颜）
    "移": 1,
}


def _resolve_model(db: Session, model_id, model_type: ModelType, scene_code: str) -> Model:
    if model_id:
        m = db.get(Model, model_id)
        if not m or not m.is_enabled:
            raise ValueError("模型不存在或已停用")
        return m
    # 优先：默认模型且匹配 scene_code（必须同时生效）
    m = db.scalar(
        select(Model).where(
            Model.model_type == model_type,
            Model.is_default.is_(True),
            Model.is_enabled.is_(True),
            Model.scene_codes.contains([scene_code]),
        ).order_by(Model.sort.asc())
    )
    if m:
        return m
    # 存在默认模型但被停用：明确报错，禁止静默回退到其它模型顶替
    # （2026-08-07：Flux2Klein 默认但未生效时关键帧被回退到 Agnes，用户要求
    #  必须依据「勾选了默认且生效」的模型使用）
    default_disabled = db.scalar(
        select(Model).where(
            Model.model_type == model_type,
            Model.is_default.is_(True),
            Model.is_enabled.is_(False),
            Model.scene_codes.contains([scene_code]),
        )
    )
    if default_disabled:
        raise ValueError(
            f"默认模型「{default_disabled.name}」已停用，"
            f"请先在模型管理中启用，或为「{scene_code}」重新指定默认模型"
        )
    # 回退：无默认模型时，任意启用的匹配 scene_code 的模型
    # 按 sort 升序保证选择确定性（避免无 order_by 时 PostgreSQL 返回顺序不确定）
    m = db.scalar(
        select(Model).where(
            Model.model_type == model_type,
            Model.is_enabled.is_(True),
            Model.scene_codes.contains([scene_code]),
        ).order_by(Model.sort.asc())
    )
    if not m:
        raise ValueError(f"未配置可用的「{scene_code}」模型，请先在模型管理启用并设默认")
    return m


def _select_view_for_shot(asset: Asset, shot_type: str | None, camera: str | None) -> str | None:
    """按景别+运镜选择最合适的四视图；无四视图则回退封面/四格合一图。

    2026-08-09：四视图新链路产出 character_sheet_url（单张四格合一图：半身特征格+
    正面/侧面/背面全身）→ 无旧 four_view_urls 分图时回退 character_sheet_url（仍含
    正面/侧面/背面全身，优于仅封面），再回退封面。
    """
    if not asset.four_view_urls:
        sheet = getattr(asset, "character_sheet_url", None) or ""
        return sheet or asset.cover_url
    idx = None
    if camera and camera in _CAMERA_VIEW_INDEX:
        idx = _CAMERA_VIEW_INDEX[camera]
    elif shot_type and shot_type in _SHOT_TYPE_VIEW_INDEX:
        idx = _SHOT_TYPE_VIEW_INDEX[shot_type]
    if idx is None or idx >= len(asset.four_view_urls) or not asset.four_view_urls[idx]:
        return asset.four_view_urls[0] or asset.cover_url
    return asset.four_view_urls[idx]


def _resolve_prev_keyframe_url(db: Session, segment: Segment) -> str | None:
    """P6 关键帧链式：取上一镜（index-1）最新成功关键帧 URL；无则 None。

    链式生成：下一镜关键帧以上一镜关键帧为主参考，人物/服装/光影逐镜延续，
    配合视频首尾帧衔接实现整幕连贯。换场景时建议关闭（见 _resolve_ref_urls 注释）。
    """
    prev = db.scalar(
        select(Segment).where(
            Segment.episode_id == segment.episode_id,
            Segment.index < segment.index,
        ).order_by(Segment.index.desc()).limit(1)
    )
    if prev is None:
        return None
    kf = db.scalar(
        select(Keyframe).where(
            Keyframe.segment_id == prev.id,
            Keyframe.status == MediaStatus.succeeded,
            Keyframe.image_url.isnot(None),
        ).order_by(Keyframe.index.desc()).limit(1)
    )
    if kf is None:
        return None
    # 2026-08-08 防护：上一镜关键帧文件可能已被删除/清理（DB 行还在但磁盘文件丢失），
    # 直接引用会导致下游任务报「参考图文件不存在」整镜失败。文件缺失则跳过该参考，
    # 由角色/场景/道具资产图兜底（否则整个分镜无法生成）。
    marker = "/static/media/"
    if marker in kf.image_url:
        from app.config import settings

        local_path = os.path.join(settings.media_dir, kf.image_url.split(marker, 1)[1])
        if not os.path.exists(local_path):
            logger.warning(
                "上一镜关键帧 %s 文件已不存在（%s），跳过 prev_kf 链式参考", kf.id, local_path
            )
            return None
    return kf.image_url


def _resolve_refs_with_labels(db: Session, segment: Segment, payload: KeyframeGenerate) -> list[tuple[str, str]]:
    """解析关键帧参考图 URL + 语义标签（角色名/场景名/道具名），供多图指代。

    返回 [(url, label), ...]：label 描述该参考图的语义（如「角色「周远」」），
    provider 据此生成 "Image N: <label>" 指代块，让 FLUX.2 知道每张参考图是什么。
    有参考图 → 走 img2img 保证一致性；无 → 回退纯文生图。
    """
    if payload.ref_image_urls is not None:
        return [(u, f"参考图{i + 1}") for i, u in enumerate(payload.ref_image_urls)]
    if not payload.use_reference:
        return []

    # 角色：按景别+运镜选择对应的四视图分图（正面/侧面/背面/特写）作参考
    character_refs: list[tuple[str, str]] = []
    for cid in segment.character_ids:
        try:
            asset = db.get(Asset, uuid.UUID(str(cid)))
        except (ValueError, TypeError):
            continue
        if asset:
            view_url = _select_view_for_shot(asset, segment.shot_type, segment.camera)
            if view_url:
                character_refs.append((view_url, f"角色「{asset.name}」"))

    # 场景封面
    scene_pair = None
    if segment.scene_id:
        try:
            scene = db.get(Asset, uuid.UUID(segment.scene_id))
        except (ValueError, TypeError):
            scene = None
        if scene and scene.cover_url:
            # 关键帧 img2img 参考图权重高，六格网格图会直接被画进画面（与 R2V 同问题）；
            # 且关键帧是单视角静态图，用封面引导构图即可，不引用六格图。
            scene_pair = (scene.cover_url, f"场景「{scene.name}」")

    # 道具封面
    prop_refs: list[tuple[str, str]] = []
    for pid in segment.prop_ids:
        try:
            prop = db.get(Asset, uuid.UUID(str(pid)))
        except (ValueError, TypeError):
            continue
        if prop and prop.cover_url:
            prop_refs.append((prop.cover_url, f"道具「{prop.name}」"))

    # 2026-08-08 用户拍板：所选资产（角色/场景/道具）优先作主参考，上一镜关键帧
    # 放末尾仅辅助光影/服装延续。此前 prev_kf 占 Image 1 主参考位，其画面主导输出
    # 会覆盖用户所选资产（实测 seg2 林浅形象被上一镜低马尾带偏，未按资产执行）。
    prev_kf_url = _resolve_prev_keyframe_url(db, segment)
    ordered = [("c", u) for u, _ in character_refs]
    ordered += [("s", scene_pair[0])] if scene_pair else []
    ordered += [("p", u) for u, _ in prop_refs]
    ordered += [("prev_kf", prev_kf_url)] if prev_kf_url else []
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, u in ordered:
        if not u or u in seen:
            continue
        seen.add(u)
        if kind == "prev_kf":
            label = "上一镜关键帧（人物/服装/光影延续参考）"
        elif kind == "c":
            label = next((l for uu, l in character_refs if uu == u), "角色参考")
        elif kind == "s":
            label = scene_pair[1]
        else:
            label = next((l for uu, l in prop_refs if uu == u), "道具参考")
        refs.append((u, label))
    # 不在此截断：参考图上限由 generate 按所选模型能力校验（超过报错，不静默丢弃）
    return refs


def _resolve_ref_urls(db: Session, segment: Segment, payload: KeyframeGenerate) -> list[str]:
    """解析关键帧参考图 URL（兼容旧接口）：仅取 URL，标签见 _resolve_refs_with_labels。"""
    return [u for u, _ in _resolve_refs_with_labels(db, segment, payload)]


def _missing_character_assets(db: Session, segment: Segment) -> list[str]:
    """返回分镜涉及但未选为资产参考图的角色名。

    2026-08-08 用户拍板：画面/对白涉及的角色必须有资产参考图，否则形象随机、
    画面不符合预期（实测 seg2 描述周远却未选周远资产 → 周远无参考、形象失控）。
    缺失时报错拦截，由用户补选资产后重试。

    「涉及角色」判定收紧（2026-08-08 修复误报）：只认两类——
    1. 对白开口角色（speaker，画面中开口者必然出镜且形象需一致，权威源）；
    2. 描述中的画面主体角色（「拍摄/聚焦/对准」等强主体词后紧邻的角色名）。
    不再把描述文本中任何出现的角色名都视为涉及（如「将钥匙递向林浅」的林浅
    仅是动作对象、非画面主体，误报导致用户勾选资产仍被拦截）。
    """
    chosen: set[str] = set()
    for cid in segment.character_ids or []:
        try:
            a = db.get(Asset, uuid.UUID(str(cid)))
        except (ValueError, TypeError):
            continue
        if a and a.name:
            chosen.add(a.name)
    involved: set[str] = set()
    # 1) 对白说话人：画面中开口的角色必须有形象参考。
    #    旁白/叙述者不出镜，无需角色资产参考图（2026-08-18 修复：此前把
    #    speaker=旁白 误判为必须出镜的角色，导致关键帧被错误拦截）。
    # 2026-08-19：画外音说话人不必出镜，也纳入免检——电话/手机语音（陌生男声、
    # 陌生女声、电话、来电、对讲、广播等）只闻其声、画面中不出现该说话人，
    # 不应要求为其补选角色资产参考图（实测 13 镜「陌生男声」电话威胁被误拦截）。
    NON_VISUAL_SPEAKERS = (
        "旁白", "叙述", "叙述者", "旁白音", "旁白者", "narrator", "narration",
        "画外音", "voiceover", "voice over", "vo",
        "陌生男声", "陌生女声", "男声", "女声", "电音", "变声",
        "电话", "来电", "手机", "对讲机", "对讲", "广播", "喇叭", "扩音器",
        "电台", "收音机", "铃", "短信", "信息",
    )
    project_chars = db.scalars(
        select(Asset).where(
            Asset.project_id == segment.episode.project_id,
            Asset.type == AssetType.character,
        )
    ).all()
    for dl in (segment.dialogue_lines or []):
        sp = (dl.get("speaker") or "").strip()
        if not sp:
            continue
        if sp in NON_VISUAL_SPEAKERS:
            continue
        # 子串兜底：speaker 名含「声/音/电话/广播」等画外音特征且不是项目角色
        if any(k in sp for k in ("声音", "声", "画外", "电话", "广播", "对讲", "音效")):
            if not any(a.name == sp for a in project_chars):
                continue
        involved.add(sp)    # 2) 描述中的画面主体：拍摄/聚焦/对准等强主体词后紧邻的角色名
    desc = segment.description or ""
    for a in project_chars:
        if not a.name:
            continue
        name = a.name
        if (
            re.search(rf"(?:拍摄|聚焦|对准|定格|镜头对准|特写)\s*{re.escape(name)}", desc)
            or f"{name}独自" in desc
            or f"{name}一人" in desc
        ):
            involved.add(name)
    return sorted(involved - chosen)


def list_by_segment(db: Session, segment_id):
    return db.scalars(
        select(Keyframe).where(Keyframe.segment_id == segment_id).order_by(Keyframe.index.asc())
    ).all()


def _validate_generate(db: Session, segment_id, payload: KeyframeGenerate):
    """generate/regenerate 共用的无副作用前置校验。

    校验项：分镜存在 / 资产参考图 / 对白与画面涉及角色必须有资产 / 模型可用 /
    参考图数量上限。2026-08-15（H2）：regenerate 必须先校验后删除旧产物，
    校验失败时保留用户已有成果，避免先删后验导致数据丢失。
    """
    segment = db.get(Segment, segment_id)
    if not segment:
        raise ValueError("分镜不存在")
    # 解析参考图：关键帧必须基于所选资产（角色/场景/道具）做图生图，
    # 不支持无参考的纯文生图——无资产直接报错提示先选资产（2026-08-08 用户拍板）。
    ref_pairs = _resolve_refs_with_labels(db, segment, payload)
    ref_urls = [u for u, _ in ref_pairs]
    ref_labels = [l for _, l in ref_pairs]
    if not ref_urls:
        raise ValueError("关键帧需先选择角色/场景/道具等资产作为参考图")
    # 2026-08-08 用户拍板：画面/对白涉及的角色必须有资产参考图，缺则拦截提示补选
    # （仅自动参考模式生效；用户显式指定 ref_image_urls 时尊重用户指定）。
    if payload.ref_image_urls is None:
        missing = _missing_character_assets(db, segment)
        if missing:
            raise ValueError(
                f"分镜涉及角色「{'、'.join(missing)}」但未选为资产参考图，"
                f"请先在资产区补充选择后再生成"
            )
    model = _resolve_model(db, payload.model_id, ModelType.image, "img2img")
    # 参考图上限按模型能力：FLUX.1 dev + XLabs IPAdapter(ref_engine=flux_ipadapter) 官方
    # 2-3 张最优 → 上限 3；Flux.2 Klein(ref_engine=flux2) 官方 4 张；Z-Image Turbo 等
    # 多图参考（≥2 张）必出马赛克（2026-08-07 对照实验），仅支持单图。
    # 超过上限明确报错，禁止静默降级截断（2026-08-08 用户拍板）。
    cap = getattr(model, "capability", None) or {}
    ref_max = {
        "flux_ipadapter": 3,
        "flux2": 4,
        "flux2_9b_gguf": 4,
    }.get(cap.get("ref_engine"), 1)
    if len(ref_urls) > ref_max:
        raise ValueError(
            f"参考图数量（{len(ref_urls)} 张）超过模型「{model.name}」上限"
            f"（{ref_max} 张），请精简所选资产"
        )
    return segment, model, ref_urls, ref_labels


def generate(db: Session, segment_id, payload: KeyframeGenerate):
    from app.tasks.generate_keyframe import generate_keyframe

    # 前置校验（与 regenerate 共用同一份逻辑，保证两者行为一致）
    segment, model, ref_urls, ref_labels = _validate_generate(db, segment_id, payload)
    project_id = segment.episode.project_id
    project = segment.episode.project
    # FLUX.1 dev / Flux.2 Klein 等英文原生模型（capability.ref_engine 非空）输出中英双语 prompt
    is_bilingual = bool((getattr(model, "capability", None) or {}).get("ref_engine"))
    # P4：LLM 提示词增强（含角色/场景/道具/风格/镜头语言/负面词，缓存分镜级复用）
    # 用户手动编辑过 prompt（≠分镜描述）时尊重自定义，不覆盖
    is_custom = bool(
        payload.prompt and payload.prompt.strip() != (segment.description or "").strip()
    )
    if is_custom:
        enriched_prompt = payload.prompt
    else:
        # 2026-08-10：LLM 提示词增强移入 worker（generate_keyframe 任务内生成），
        # 避免 API 请求线程同步调 LLM（~5-15s）导致「点击生成后等好几秒才派发」。
        # 与视频链路 2026-08-10 修复一致；prompt 留空由 worker 增强，缓存分镜级复用。
        enriched_prompt = ""
    idx = (db.scalar(select(func.max(Keyframe.index)).where(Keyframe.segment_id == segment_id)) or 0) + 1
    kf = Keyframe(
        segment_id=segment_id, index=idx, prompt=enriched_prompt,
        model_id=model.id, status=MediaStatus.pending,
    )
    db.add(kf)
    db.flush()

    task = Task(
        project_id=project_id, type=TaskType.generate_keyframe,
        target_type="keyframe", target_id=kf.id, model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    kf.task_id = task.id  # 回填，供前端按 media.task_id 轮询
    db.commit()
    db.refresh(kf)
    db.refresh(task)
    generate_keyframe.delay(str(task.id), ref_image_urls=ref_urls or None, ref_labels=ref_labels or None)
    return kf, task


def regenerate(db: Session, keyframe_id, payload: KeyframeGenerate):
    kf = db.get(Keyframe, keyframe_id)
    if not kf:
        raise ValueError("关键帧不存在")
    segment_id = kf.segment_id
    # 在删除旧关键帧前缓存 prompt：commit 后 kf 变为已删除对象，直接访问 kf.prompt
    # 会抛 ObjectDeletedError（用户未改 prompt 时 payload.prompt 为空，会触发解引用）
    old_prompt = kf.prompt
    new_payload = KeyframeGenerate(
        prompt=payload.prompt or old_prompt,
        model_id=payload.model_id,
        size=payload.size,
        ratio=payload.ratio,
        use_reference=payload.use_reference,
        ref_image_urls=payload.ref_image_urls,
    )
    # H2 修复（2026-08-15）：删除旧产物前先用 generate 同一套校验预检，
    # 模型停用/资产缺失/超上限等失败时保留用户已有关键帧（原实现先删后验会丢数据）。
    _validate_generate(db, segment_id, new_payload)
    # 重新生成：删除该分镜全部旧关键帧（DB 行 + 磁盘图片文件 + 取消未完成任务），
    # 保证每分镜关键帧唯一（前端只展示最新一张）。
    delete_segment_keyframes(db, segment_id)
    db.commit()
    return generate(db, segment_id, new_payload)


def delete_segment_keyframes(db: Session, segment_id) -> int:
    """删除分镜下全部关键帧（DB 行 + 磁盘图片文件 + 取消任务），返回删除条数。

    供重新生成/批量重跑调用，保证每分镜只保留一张最新关键帧。
    """
    kfs = db.scalars(select(Keyframe).where(Keyframe.segment_id == segment_id)).all()
    for k in kfs:
        _delete_keyframe(db, k)
    return len(kfs)


def delete(db: Session, keyframe_id) -> bool:
    kf = db.get(Keyframe, keyframe_id)
    if not kf:
        return False
    _delete_keyframe(db, kf)
    db.commit()
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
        t.status = TaskStatus.cancelled
        t.error = "旧任务已取消（关键帧被重新生成/删除）"


def _delete_keyframe(db: Session, kf: Keyframe) -> None:
    """删除单个关键帧：取消任务 + 删除磁盘图片文件 + 删 DB 行（不 commit，由调用方提交）。"""
    _cancel_related_task(db, kf.task_id)
    delete_media_file(kf.image_url)
    db.delete(kf)
