"""视频生成任务：调 video Provider 图生视频（异步轮询）→ 下载落库。

2026-08-10 纯资产图直接出片（用户拍板，替代关键帧先行）：
- 分镜视频无需首帧/关键帧——R2V 以资产参考图直接出片，
  场景封面作 ref_image_0 构图锚点（首帧≈场景封面属预期，不匹配画面后期剪辑处理）。
- 有关键帧时仍优先用作首帧锚定构图（ref_image_0 = 关键帧，画面更可控）。
- R2V 参考图组装 = 场景封面 + 角色四视图合一图 + 道具 cover（≤ ref_max 张）。
"""
import logging
import os
import subprocess
import uuid

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.media import Keyframe, MediaStatus, VideoClip
from app.models.model_config import Model
from app.models.segment import Segment
from app.models.task import Task, TaskStatus
from app.providers.base import VideoOpts
from app.providers.errors import ProviderError, map_to_chinese
from app.providers.registry import ProviderRegistry
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app

from app.services.prompt_enhance_service import ensure_enhanced_prompt
from app.services.style_service import get_style_video_params

logger = logging.getLogger(__name__)


def _resize_frame(media_url: str, w: int, h: int) -> str | None:
    """把媒体图片缩放到 w×h（H3 I2V 硬首帧要求首帧图与输出尺寸一致），返回新 /static/media URL。"""
    _MARKER = "/static/media/"
    if not media_url or _MARKER not in media_url:
        return media_url
    rel = media_url.split(_MARKER, 1)[1]
    src = os.path.join(settings.media_dir, rel)
    if not os.path.isfile(src):
        return media_url
    rel_dir = os.path.join("chained_frames", "_resized", f"{w}x{h}")
    out_rel = os.path.join(rel_dir, os.path.basename(rel))
    out = os.path.join(settings.media_dir, out_rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
             out],
            check=True, capture_output=True, text=True, timeout=120,
        )
        if not os.path.isfile(out):
            return media_url
        return f"{_MARKER}{out_rel}"
    except Exception as e:  # noqa: BLE001
        logger.warning("[video] 首帧缩放失败 base=%s: %s", src, e)
        return media_url


def _extract_video_last_frame(media_url: str, base_name: str) -> str | None:
    """从成片视频抽取末帧 PNG（"上一分镜尾帧"衔接用），返回 /static/media 媒体 URL；失败返回 None。

    2026-09-01 修复：prev_tail 上一镜只有 video_url（无 last_frame_url 图片，该字段仅在下镜有
    成功关键帧时才回填）时，旧实现把整条 mp4 当首帧/参考图上传 → 衔接失效。改为 ffmpeg -sseof
    抽取真实末帧落盘 media/chained_frames/<base>/prev_last.png，再由 provider 走图片上传。
    """
    _MARKER = "/static/media/"
    if not media_url or _MARKER not in media_url:
        return None
    rel = media_url.split(_MARKER, 1)[1]
    src_local = os.path.join(settings.media_dir, rel)
    if not os.path.isfile(src_local):
        return None
    rel_dir = os.path.join("chained_frames", base_name)
    dest_local = os.path.join(settings.media_dir, rel_dir, "prev_last.png")
    os.makedirs(os.path.dirname(dest_local), exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.2",
             "-i", src_local, "-frames:v", "1", "-update", "1", dest_local],
            check=True, capture_output=True, text=True, timeout=120,
        )
        if not os.path.isfile(dest_local):
            logger.warning("[video] 上一镜尾帧未生成 dest=%s", dest_local)
            return None
        return f"{_MARKER}{rel_dir}/prev_last.png"
    except Exception as e:  # noqa: BLE001
        logger.warning("[video] 上一镜尾帧抽取失败 base=%s: %s", base_name, e)
        return None


# H3 I2V 硬首帧合法的分辨率档位：宽/高均为 32 的倍数（latent /16 + patch2）。
# 720p 的 720/16=45 为奇数无法 patchify → 不在此列。
# H3 可直接 patchify 的生成档（32 倍数）：官方 0.5mp/0.7mp + 480p/768p。
# 720p/1080p 非 32 倍数，不能直接作 H3 生成档（720p 回退到最近生成档，1080p 仅超分）。
# 2026-09-16 补全 0.1MP~1.0MP 级联档（均 32 倍数），与前端/后端/_MMAX_RES_GRADES 同步
_VIDEO_GRADES_32_SAFE = ("0.1mp", "0.2mp", "0.25mp", "0.3mp", "0.4mp", "0.5mp", "0.6mp", "0.7mp", "0.8mp", "0.9mp", "1.0mp", "480p", "768p")


def resolve_prev_tail_grade(eff_res: str | None, model_mmax_grade: str | None) -> str:
    """H3 I2V prev_tail 目标分辨率档位：项目/分镜有效分辨率若是 32 倍数安全档
    (480p/768p) 则沿用，保证 prev_tail 镜头与同一项目其余镜头分辨率一致；
    否则（如项目为 720p，H3 无法 patchify）回退模型 minimax_res 档位。
    """
    from app.providers.comfyui import _MMAX_RES_GRADES

    eff = (eff_res or "").strip().lower()
    grade = eff if eff in _VIDEO_GRADES_32_SAFE else (model_mmax_grade or "").lower()
    if grade not in _MMAX_RES_GRADES:
        grade = "0.7mp"
    return grade


def build_prev_tail_continuity(prev_seg_desc: str | None) -> str:
    """prev_tail（首帧=上一镜尾帧）时的提示词承接块。

    声明「以首帧为准、从首帧平滑过渡到本镜内容」，压制「上一镜尾帧内容与本镜提示词
    开端不一致 → 模型把两种内容硬拼 → 面部扭曲/画面突变」的错乱。
    """
    block = (
        "【承接上一镜·首帧已锁定（场景绝对锁定）】本镜首帧直接采用上一镜尾帧画面，"
        "**本镜的场景/环境/背景/光线/机位布局 = 首帧画面中的场景，必须与首帧完全一致、"
        "不得重建、不得改变、不得新增场景元素**。后面出现的任何文字描述（含本镜提示词）"
        "若与首帧场景冲突，一律以首帧画面为准，严禁照文字另起一个场景；"
        "本镜描述只负责叙述**人物动作、表情、镜头运动与姿态变化**，不得用来描述或再造场景；"
        "人物/机位/光线从首帧状态无缝继续：即使本镜提示词开端情绪/景别与首帧不同，"
        "也一律以首帧为准，从首帧自然、平滑地过渡，面部/表情/身体变化流畅，"
        "不扭曲、不畸变、不抽搐，不得出现与首帧矛盾的画面开端；"
        "镜头与景别必须从首帧的构图直接继续：首帧若已是特写/近景，则本镜保持该景别"
        "自然推进或轻微跟随，严禁为了表现「推近/拉远」而先大幅拉远到全身/远景、"
        "再冲回特写——不得出现特写→全身→特写式的景别突跳或硬切。"
    )
    if prev_seg_desc:
        block += f"上一镜结束状态参考（仅作人物姿态/位置/情绪衔接，**场景仍以首帧为准**）：{prev_seg_desc[:120]}。"
    return block


def resolve_shot_asset_refs(
    db, segment, ref_max: int = 8, view_ref: bool = True,
) -> list[tuple[str, str]]:
    """分镜级 R2V 参考图：场景封面 + 六格机位格子 + 角色四视图 + 道具 cover。

    返回 [(url, label), ...]：label 为该参考图的语义职责（供 LLM 增强 prompt 里以
    "Image N: <label>" 明确指代；多视图网格图才不会入画）。

    2026-08-10 纯资产图直接出片。2026-08-30 机位格子参考（view_ref=True 默认）：
    - 场景：封面 ref_image_0 构图锚 + 从场景多视图 3x2 按分镜机位关键词裁出对应格子；
    - 角色：四视图整张外貌锚 + 按机位裁 2x2 四视图对应格子（增强，数量允许时）；
    - 道具：cover 外观参考。
    - 参考数量控制在安全区间（实测 7 张卡死、4-5 张正常），防 KSampler 采样卡死。
    """
    from app.models.asset import Asset
    from app.services.view_cell_selector import (
        pick_character_cell,
        pick_scene_cell,
    )

    refs: list[tuple[str, str]] = []

    def _add(u, label):
        if u and u not in [x[0] for x in refs]:
            refs.append((u, label))

    def _get(aid) -> Asset | None:
        try:
            return db.get(Asset, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            return None

    # 分镜机位语义来源：描述 + 增强 prompt 缓存 + 景别 + 运镜 + 各拍内容
    hit_texts = []
    if getattr(segment, "description", None):
        hit_texts.append(segment.description)
    if getattr(segment, "enhanced_prompt", None):
        hit_texts.append(segment.enhanced_prompt)
    if getattr(segment, "shot_type", None):
        hit_texts.append(segment.shot_type)
    if getattr(segment, "camera", None):
        hit_texts.append(segment.camera)
    for b in getattr(segment, "shot_beats", None) or []:
        if isinstance(b, dict) and b.get("content"):
            hit_texts.append(str(b["content"]))
    hit_texts = [t for t in hit_texts if t]

    # 场景：封面（构图锚）+ 机位格子（view_ref 开启且场景多视图存在时）
    if segment.scene_id:
        a = _get(segment.scene_id)
        if a:
            if a.cover_url:
                _add(a.cover_url, f"场景「{a.name}」透视图（构图锚点）")
            sheet = getattr(a, "scene_sheet_url", None) or ""
            if view_ref and sheet:
                shots = getattr(a, "scene_shots", None) or []
                cell_names, cell_texts = [], []
                if isinstance(shots, list) and len(shots) >= 6:
                    cell_names = [s.get("name", "") for s in shots[:6] if isinstance(s, dict)]
                    cell_texts = [s.get("view_text", "") for s in shots[:6] if isinstance(s, dict)]
                else:
                    from app.tasks.generate_scene_multiview import (
                        _POV_SHOTS_INDOOR,
                        _POV_SHOTS_OUTDOOR,
                        _classify_space,
                    )
                    space = "indoor"
                    try:
                        space = _classify_space(
                            getattr(a, "description", None) or "",
                            getattr(a, "expanded_description", None) or "",
                            getattr(a, "name", None) or "",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    default_shots = _POV_SHOTS_INDOOR if space == "indoor" else _POV_SHOTS_OUTDOOR
                    cell_names = [s.get("name", "") for s in default_shots]
                    cell_texts = [s.get("view_text", "") for s in default_shots]
                hit = pick_scene_cell(
                    sheet, cell_names, cell_texts, hit_texts, prefix="shot_scene",
                )
                if hit:
                    url, view, _idx = hit
                    _add(url, f"场景「{a.name}」{view}机位格子（本镜机位参考）")
    for cid in segment.character_ids or []:
        a = _get(cid)
        if not a:
            continue
        sheet = getattr(a, "character_sheet_url", None) or ""
        if sheet:
            _add(sheet, f"角色「{a.name}」四视图（外貌/服装/姿态锚点）")
            if view_ref and hit_texts:
                hit = pick_character_cell(sheet, hit_texts, prefix="shot_char")
                if hit and len(refs) < int(ref_max or 8) - 1:
                    url, view, _idx = hit
                    _add(url, f"角色「{a.name}」{view}机位格子（本镜朝向参考）")
        else:
            _add(a.cover_url, f"角色「{a.name}」（外貌参考）")
    # 道具参考恒排最后，绝不充当 ref_image_0（构图锚点）——道具封面通常是「纯白底孤立
    # 物品图」，若被用作 R2V 的构图锚，模型会把这张产品图整张渲染进画面/第一帧（见用户
    # 反馈「道具参考图在第一帧完整展示」）。道具只作外观/材质/尺寸参考，排在场景/角色之后。
    _prop_refs: list[tuple[str, str]] = []
    for pid in segment.prop_ids or []:
        a = _get(pid)
        if a and a.cover_url:
            _prop_refs.append((a.cover_url, f"道具「{a.name}」（外观/材质/尺寸参考，仅供确定道具形态，严禁以孤立物品图/产品图/白底悬浮/画框/照片形式入画）"))
    # 参考上限：场景/角色锚优先占满，道具恒在尾段补足（绝不占据 ref_image_0 锚位）。
    _limit = max(1, int(ref_max or 8))
    refs = refs[:_limit]
    _prop_cap = max(0, _limit - len(refs))
    return refs + _prop_refs[:_prop_cap]

def _resolve_text2video_model(db, preferred: Model | None = None) -> Model | None:
    """解析「无资产可参考」时的 T2V 文生视频模型。

    2026-08-28 用户规则：有资产→参考资产（R2V）；无资产→提示词文生视频（T2V）。
    T2V 需要 video_kind=minimax（img2vid 模板支持无首帧纯文生）；首选与当前模型同 provider
    的启用模型，其次任意启用的 T2V 模型；preferred 本身若是 T2V 则直接复用。
    """
    from sqlalchemy import select as _select

    if preferred is not None:
        _cap = preferred.capability or {}
        if (preferred.model_type or "") == "video" and preferred.is_enabled                 and _cap.get("video_kind") in ("minimax", None):
            return preferred

    rows = db.scalars(
        _select(Model).where(
            Model.model_type == "video",
            Model.is_enabled.is_(True),
            Model.scene_codes.contains(['video']),
        ).order_by(Model.is_default.desc(), Model.sort.asc())
    ).all()
    for m in rows:
        cap = m.capability or {}
        if cap.get("video_kind") in ("minimax", None):
            return m
    return None


@celery_app.task(name="generate_video", bind=True)
def generate_video(self, task_id: str):
    db = SessionLocal()
    target_id = None  # 提前捕获，避免 rollback 后读过期 task 对象触发 ObjectDeletedError
    try:
        task = db.get(Task, task_id)
        if task is None:
            return  # 任务行已被级联删除，无需处理
        target_id = task.target_id
        clip = db.get(VideoClip, target_id)
        if clip is None:
            return  # 视频片段已被删除
        kf = db.get(Keyframe, clip.keyframe_id) if clip.keyframe_id else None
        model = db.get(Model, task.model_id)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        clip.status = MediaStatus.running
        db.commit()

        # 2026-08-22 用户可配置首帧来源（gen_params.reference_src）：
        #   none      → 默认（clip 首帧/关键帧），不强制
        #   keyframe  → 该分镜最新成功关键帧图
        #   prev_tail → 本项目前一镜的最后帧（last_frame 优先，回退关键帧）
        #   custom    → gen_params.custom_first_frame_url（用户上传）
        # (segment 在上面才声明，此处先由旧值承接，下方 segment 声明后统一解析)
        first_frame = clip.first_frame_url or (kf.image_url if kf else None)
        segment = clip.segment
        project = segment.episode.project
        gen_params = segment.gen_params or {}
        ref_src = str(gen_params.get("reference_src") or "none")
        prev_seg_desc = None  # 承接上一镜的上下文（prev_tail 分支回填，供提示词一致性处理）
        if ref_src == "custom" and gen_params.get("custom_first_frame_url"):
            first_frame = str(gen_params["custom_first_frame_url"])
        elif ref_src == "keyframe":
            kf_latest = db.scalar(
                select(Keyframe)
                .where(Keyframe.segment_id == segment.id, Keyframe.status == MediaStatus.succeeded)
                .order_by(Keyframe.created_at.desc())
            )
            if kf_latest and kf_latest.image_url:
                first_frame = kf_latest.image_url
        elif ref_src == "prev_tail":
            # 按 幕序+分镜序 找到前一镜
            from app.models.project import Episode
            # 简化：按创建序取前一镜（同幕内 index 升序遍历）
            ep_id = segment.episode_id
            prev_seg = None
            ep = db.get(Episode, ep_id)
            if ep is not None:
                same_ep = db.scalars(
                    select(Segment).where(Segment.episode_id == ep_id).order_by(Segment.index.asc())
                ).all()
                pos = next((i for i, s in enumerate(same_ep) if s.id == segment.id), None)
                if pos is not None and pos > 0:
                    prev_seg = same_ep[pos - 1]
                elif pos == 0 and ep.index > 0:
                    prev_ep = db.scalar(
                        select(Episode).where(Episode.project_id == project.id, Episode.index == ep.index - 1)
                    )
                    if prev_ep is not None:
                        prev_seg = db.scalars(
                            select(Segment).where(Segment.episode_id == prev_ep.id).order_by(Segment.index.desc())
                        ).first()
            if prev_seg is not None:
                prev_vid = db.scalar(
                    select(VideoClip).where(
                        VideoClip.segment_id == prev_seg.id,
                        VideoClip.status == MediaStatus.succeeded,
                    ).order_by(VideoClip.created_at.desc())
                )
                if prev_vid and prev_vid.last_frame_url:
                    first_frame = prev_vid.last_frame_url
                elif prev_vid and prev_vid.video_url:
                    # 修复（2026-09）：prev_tail 上一镜无尾帧图（last_frame_url 仅在下镜有关键帧
                    # 时才回填）时，旧实现把整条 mp4 当首帧/参考图上传 → 衔接失效。
                    # 改为从成片视频抽取真实末帧 PNG 作为本镜首帧。
                    first_frame = _extract_video_last_frame(prev_vid.video_url, base_name=str(prev_vid.id))
            if prev_seg is not None:
                # 记录上一镜描述：提示词里声明「承接上一镜」，避免首帧(上一镜尾帧)与本镜
                # 提示词开端的情绪/景别不一致 → 模型把两种内容硬拼接导致画面错乱。
                prev_seg_desc = (getattr(prev_seg, "description", None) or "").strip() or None
        # 2026-09（用户拍板）prev_tail 真正"首帧接上一镜尾帧"：上一镜尾帧作为**硬首帧**走
        # I2V（MiniMaxH3ImageToVideo.first_frame），而不是 R2V 的参考图（参考图只做引导、
        # 不锁定首帧 → 画面衔接断裂）。故切换到 img2vid(video_kind=minimax) 模型并跳过多参考
        # 资产（人物/场景一致性由首帧本身继承，反而更稳）。
        if ref_src == "prev_tail" and first_frame:
            _img2v = _resolve_text2video_model(db, preferred=model)
            if _img2v is not None:
                model = _img2v
                clip.model_id = _img2v.id
                # H3 I2V 硬首帧要求 width/height 为 32 的倍数（latent /16 + patch2），
                # 且首帧图尺寸须与输出一致；720×1280 的 720 不是 32 倍数 → 真机 latent 形状报错。
                # 2026-09 修复：优先沿用项目/分镜有效分辨率档位（480p/768p 均为 32 倍数对齐），
                # 保证 prev_tail 镜头与同一项目其余镜头分辨率一致；仅当项目为 720p（H3 无法
                # patchify，720/16=45 奇数）时才回退到模型 minimax_res 档位。
                _eff_res = str(
                    gen_params.get("res")
                    or (project.video_params if project else {}).get("res")
                    or (project.resolution if project else "")
                    or ""
                )
                _target_res = resolve_prev_tail_grade(
                    _eff_res,
                    (_img2v.capability or {}).get("minimax_res", "0.7mp"),
                )
                from app.providers.comfyui import _MMAX_RES_GRADES
                _grade = _MMAX_RES_GRADES.get(_target_res, _MMAX_RES_GRADES["0.7mp"])
                _w = int(clip.width or 768); _h = int(clip.height or 768)
                _ratio = "1:1"
                if abs(_w / _h - 16 / 9) <= 0.06:
                    _ratio = "16:9"
                elif abs(_w / _h - 9 / 16) <= 0.06:
                    _ratio = "9:16"
                elif abs(_w / _h - 4 / 3) <= 0.06:
                    _ratio = "4:3"
                elif abs(_w / _h - 3 / 4) <= 0.06:
                    _ratio = "3:4"
                elif abs(_w / _h - 1) <= 0.06:
                    _ratio = "1:1"
                _nw, _nh = _grade.get(_ratio, _grade["9:16"])
                clip.width = _nw; clip.height = _nh
                # 强制 provider 走目标分辨率档位，否则 provider 按项目默认分辨率
                #（如 720p）把宽高又映射回 (720,1280)（非 32 倍数），与已缩放的首帧不匹配。
                gen_params["res"] = _target_res
                # 立即提交：task 后续 update_task/commit 会把 clip 过期重读，
                # 不提交则 opts 构建时仍按旧尺寸取 → 提交给 ComfyUI 又回到错误分辨率。
                db.commit()
                first_frame = _resize_frame(first_frame, _nw, _nh)
                logger.info(
                    "[video] 分镜 %s prev_tail → I2V 硬首帧衔接（模型 %s，%dx%d，档位 %s）",
                    segment.id, _img2v.name, _nw, _nh, _target_res,
                )
        # 项目级/分镜级视频分辨率：分镜级 gen_params.res 优先，其次项目级
        # video_params.res / project.resolution（0.5mp/0.7mp/480p/768p → 模型档位；
        # 720p/1080p 非 32 倍数，H3 生成回退模型 minimax_res 官方档）
        _pgp_res = (project.video_params if project else None) or {}
        res_opt = str(gen_params.get("res") or _pgp_res.get("res") or "") if gen_params else ""
        # 2026-09-09 融合模型官方档：0.5mp/0.7mp 可直接生成（32 倍数）；
        # 720p/1080p 非 32 倍数不能直接 patchify → 回退模型 minimax_res（官方主推档）。
        _model_grade = (model and model.capability or {}).get("minimax_res", "0.7mp") if model else "0.7mp"
        if res_opt in ("1080p", "720p"):
            # 超纲/非 32 倍数生成档 → 用模型官方主推档（0.7mp / 0.5mp）
            provider_res = _model_grade
        elif res_opt in _VIDEO_GRADES_32_SAFE:
            provider_res = res_opt
        else:
            provider_res = (project.resolution if project else None) or _pgp_res.get("res") or _model_grade
            if provider_res in ("720p", "1080p"):
                provider_res = _model_grade
        provider = ProviderRegistry.for_model(
            model, resolution=provider_res,
        )
        # R2V 多参考：模型 video_kind=minimax_ref 时收集分镜关联资产参考图
        # （2026-08-10 纯资产图出片：场景封面 ref_image_0 锚点 + 六格多视角图 + 角色四视图 + 道具；
        #  ref_labels 供 LLM 增强 prompt 以 Image N 指代每张参考图的职责，防六格图入画）
        reference_assets = None
        reference_labels: list[str] = []
        if model is not None and (model.capability or {}).get("video_kind") == "minimax_ref":
            ref_pairs = resolve_shot_asset_refs(
                db, segment,
                ref_max=(model.capability or {}).get("ref_max", 8),
                view_ref=bool((model.capability or {}).get("multiview_view_ref", True)),
            )
            reference_assets = [u for u, _ in ref_pairs]
            reference_labels = [l for _, l in ref_pairs]
        # 2026-08-10 用户拍板：纯资产图直接出片（跳过关键帧）。
        # 分镜视频无需首帧/关键帧——R2V 以资产参考图直接出片（场景封面作 ref_image_0 锚点）。
        # 首帧≈场景封面属预期（不匹配画面由后期剪辑处理）；有关键帧时仍优先用作首帧锚定构图。
        # 2026-08-28 用户规则：有资产→参考资产生成；无资产（且无首帧）→按提示词文生视频（T2V，
        # 不报错）。img2vid 模板原生支持无首帧纯文生（移除 LoadImage / first_frame 输入）。
        # 2026-09-02（方案1）：「无构图锚」回退——若参考序列的首个元素是「道具」（即分镜
        # 没绑场景、也没有角色参考可供构图锚定），则不能把道具封面当作 ref_image_0 构图锚
        #（孤立白底产品图会被整张渲染进第一帧）。此时回退 T2V 文生视频，道具外观由提示词描述，
        # 而非用产品图当锚。
        _no_anchor = bool(
            reference_labels and reference_labels[0].startswith("道具")
        )
        if (not first_frame and not reference_assets) or _no_anchor:
            t2v_model = _resolve_text2video_model(db, preferred=model)
            if t2v_model is None:
                raise ProviderError(
                    "该分镜无资产参考图且无首帧，且未配置可用的 T2V 视频模型"
                    "（video_kind=minimax）。请在分镜绑定角色/场景/道具资产，"
                    "或配置文生视频模型后重试"
                )
            model = t2v_model
            clip.model_id = t2v_model.id
            provider = ProviderRegistry.for_model(t2v_model, resolution=provider_res)
            reference_assets = None
            reference_labels = []
            logger.info(
                "[video] 分镜 %s 无可用构图锚（场景/角色参考缺失，参考只剩道具）→ 回退 T2V 文生视频（模型 %s）",
                segment.id, t2v_model.name,
            )
        enhanced_prompt, negative_prompt = ensure_enhanced_prompt(
            db, segment, project, target="video",
            ref_labels=reference_labels or None,
        )
        # 风格适配：按项目风格取 SigmaShift shift / steps / 风格负面词补充
        style_params = get_style_video_params(db, project)
        if style_params.get("negative_extra"):
            negative_prompt = f"{negative_prompt}, {style_params['negative_extra']}".strip(", ")
        # 视频提示词：用户未自定义（为空或等于分镜描述）→ 用 LLM 增强 prompt（含对白演绎，模型原生说话）；
        # 用户自定义 → 尊重原文（同样可含对白，不再剥离，让模型原生演绎）
        raw = clip.prompt or ""
        if not raw.strip() or raw.strip() == (segment.description or "").strip():
            video_prompt = enhanced_prompt or segment.description or ""
        else:
            video_prompt = raw
        # 确定性兜底：模型语音语言控制弱（对白默认中英自由发挥，
        # 实测"旁白画外音：台词"式叙述描述会输出英文旁白；须用「XX用中文说：「原文」」
        # 直接台词指令格式才能按中文原句朗读）。在提示词最前面放置【语言要求】+【台词原句】
        # 块（开头权重最高），并追加面部稳定约束。
        # 2026-08-07 音频修复：ComfyUI MiniMax H3（AV 联合生成）同样需要台词/旁白原句作为
        # 音频描述注入 prompt——缺音频描述时音频 latent 无文本引导，4 步下会输出撕裂低频噪声。
        if provider.provider_type in ("http_poll", "comfyui") and video_prompt:
            # 台词块：从 segment 权威源（dialogue_lines）构建，格式为直接台词指令。
            # 2026-08-09：旁白（narration）恢复朗读并注入固定音色——用户要求旁白
            # 声音可定义且全片一致（实测 MiniMax H3 原生语音旁白音色随机，两个视频
            # 一男一女）；音色取自项目 narrator_profile，与 P4 增强链路保持一致。
            # 角色声线：已定义声线档案的角色声音严格按档案执行（build_speaker_voice_map
            # 只返回有档案的角色），无档案角色不注入、模型自由发挥。
            from app.services.character_voice_service import (
                build_speaker_voice_map,
                ensure_narrator_profile,
                narrator_voice_description,
            )

            narrator_voice = ""
            if project is not None:
                # 2026-08-10：旁白音色由 LLM 按剧情生成（未配置时懒生成一次落库），
                # 避免默认「中性稳重男声」与男主同声
                narrator_voice = narrator_voice_description(
                    ensure_narrator_profile(db, project)
                )
            speaker_voices = build_speaker_voice_map(db, segment.character_ids)
            speech_lines: list[str] = []
            for dl in (segment.dialogue_lines or []):
                if not isinstance(dl, dict):
                    continue
                text = (dl.get("text") or "").strip()
                if not text:
                    continue
                speaker = (dl.get("speaker") or "").strip() or "角色"
                # 声线优先按 character_id 匹配（对白与资产名可能不一致），回退按 speaker 名
                voice_desc = (
                    speaker_voices.get(str(dl.get("character_id")))
                    or speaker_voices.get(speaker)
                    or ""
                )
                voice_suffix = f"，声线：{voice_desc}" if voice_desc else ""
                # 2026-08-10：内心独白（kind=inner）——角色本人声音画外音、嘴唇不动，
                # 与开口对白（dialogue）区分；音色仍是角色本人，不是叙述者
                kind = (dl.get("kind") or "dialogue").strip() or "dialogue"
                if kind == "inner":
                    speech_lines.append(
                        f"{speaker}内心独白（角色本人声音，画外音，画面中该角色"
                        f"嘴唇不动、不得对口型）用中文说：「{text}」{voice_suffix}"
                    )
                else:
                    speech_lines.append(
                        f"{speaker}用中文说：「{text}」{voice_suffix}"
                    )
            if segment.narration and segment.narration.strip():
                voice_hint = f"，{narrator_voice}" if narrator_voice else ""
                # 在场角色名单：旁白音色必须与所有角色拉开差异（LTX-AV/H3 原生语音
                # 音色控制弱，不点明会在场角色时可能把旁白分配给角色声线）
                onstage = "、".join(
                    d.get("speaker", "").strip()
                    for d in (segment.dialogue_lines or [])
                    if (d.get("speaker") or "").strip()
                ) or None
                speech_lines.append(
                    f"旁白（画外音{voice_hint}，由不露面的叙述者单独配音，"
                    f"旁白音色必须与画面所有角色"
                    + ("（" + onstage + "）" if onstage else "（在场角色）")
                    + "的声音明显区分，绝不使用任何角色的声音；画面中所有角色的嘴巴必须闭合、"
                    f"不得开口念旁白、不得对口型）用中文（普通话）朗读："
                    f"「{segment.narration.strip()}」"
                )
            face_guard = (
                "保持人物面部结构始终稳定不变形，五官清晰、比例协调，说话时口型自然、"
                "与台词同步、开合适度；杜绝面部扭曲、五官错位、面部变形融化；"
                "严禁嘴部抽搐、嘴巴夸张开合、牙齿错乱、嘴唇变形、闭嘴时嘴部抖动；"
                "同一时刻只有当前说话人在开口，其他在场角色保持自然表情、"
                "嘴巴不得开合或对口型。\n"
            )
            # 2026-08-09（六段式整合回归修复 H3/M6）：增强链路输出 H3 官方六段式时，
            # 台词已由 <d>[Chinese] 原句</d> 标签承载（含声线/音色/中文硬约束），
            # 若再前置「」引号台词块会与 <d> 指令互斥、台词出现两遍 → 双读/声线稀释。
            # 因此六段式链路只补「面部稳定」约束；仅无对白镜头补禁人声（六段式未覆盖）。
            # 用户自定义 prompt（非六段式）仍走原前置台词块逻辑。
            is_six_section = (
                "detailed_description" in video_prompt
                or "subject_definitions" in video_prompt
            )
            if is_six_section:
                if speech_lines:
                    video_prompt = (
                        "【对白仅发声不上屏】画面中所有台词、对白、旁白一律只通过人物开口发声"
                        "（或画外音）呈现，绝不允许以任何文字、字幕、字幕条形式出现在画面上；"
                        "画面中严禁渲染任何对白文字、字幕或文字。\n"
                        + face_guard
                        + video_prompt
                    )
                else:
                    video_prompt = (
                        "【纯画面镜头】本镜头为纯视觉叙事，画面中人物不得开口说话，"
                        "不得产生任何台词、对白、旁白或人声；仅保留环境音效"
                        "（雨声、风声、水声、脚步声等自然声音），"
                        "严禁出现任何说话声、朗读声或人声语音。\n"
                        + face_guard
                        + video_prompt
                    )
            elif speech_lines:
                # 有对白：语言硬约束 + 台词原句前置（开头权重最高），模型按原句说话
                speech_block = (
                    "【本视频必须用中文朗读的台词原句】逐字用中文说出引号「」内的文字，"
                    "不得翻译成英文或任何外语；只朗读「」内的台词本身，"
                    "绝对禁止读出说话人名字（如「林浅」）、冒号或提示词中的任何其他内容；"
                    "同一时刻只有台词对应的说话人在开口，其他在场角色必须保持安静、"
                    "嘴巴不得开合或对口型。"
                    "以上台词只作为人物发声或画外音存在，绝不允许以任何文字、字幕、"
                    "字幕条形式出现在画面任何位置；画面中严禁渲染任何对白文字、字幕或文字。\n"
                    + "\n".join(speech_lines)
                    + "\n"
                )
                video_prompt = (
                    "【语言要求】本视频所有角色语音、对白必须且只能用简体中文"
                    "（普通话，zh-CN）发出：逐字朗读上面引号「」内的中文原句，"
                    "绝对禁止翻译成英语或任何外语，禁止夹杂任何英文单词、禁止中英混杂——"
                    "出现任何英文发音或英文单词均属严重错误。\n"
                    + speech_block
                    + face_guard
                    + video_prompt
                )
            else:
                # 纯视觉镜头（无对白/旁白）：实测模型无台词指令时仍会自行编造人声
                # （"皮鞋踏水花"分镜被 whisper 检测到莫名说话声），必须显式禁止人声，
                # 只保留环境音效，否则空指令反而诱导模型"朗读"产生杂音人声
                video_prompt = (
                    "【纯画面镜头】本镜头为纯视觉叙事，画面中人物不得开口说话，"
                    "不得产生任何台词、对白、旁白或人声；仅保留环境音效"
                    "（雨声、风声、水声、脚步声等自然声音），"
                    "严禁出现任何说话声、朗读声或人声语音。\n"
                    + face_guard
                    + video_prompt
                )
        # 2026-08-09 R2V 参考图语义声明（资产参考链路）：参考图仅作外观/场景特征参考，
        # 严禁入画——实测模型会把角色四视图/场景封面直接渲染进画面并多角色乱入，
        # 必须在提示词开头（权重最高）明确参考图语义与角色数量。
        # 2026-08-10 场景参考图升级为六格多视角合一图（俯视+5POV），需说明多格用途。
        if reference_assets:
            video_prompt = (
                "本次生成将使用角色/场景/道具参考图：参考图仅供提取外观、服装、"
                "场景与道具特征，参考图本身严禁以任何形式出现在视频画面中——"
                "不得显示图片边框、缩略图、四视图网格、照片、画板、海报、截图或角色图鉴；"
                "其中场景参考图为六格多视角合一图（俯视布局图+五个平视视角），"
                "用于理解场景的空间结构与各角度外观，网格本身不是画面内容，"
                "不得在视频中渲染任何拼图、网格或分格画面；"
                "画面中只呈现本分镜描述中实际在场的角色，角色数量严格与描述一致，"
                "禁止出现参考图中的其他人物、禁止额外人物乱入、禁止角色合照；"
                "道具参考图仅用于确定道具的外观/材质/尺寸/手持方式：道具必须以合理方式"
                "出现在场景中（被手持/放下/使用/互动），**严禁以孤立物品图、产品展示、"
                "纯白底悬浮、贴纸、画框、照片、缩略图形式出现**，严禁把道具参考图本身"
                "当作画面主体或背景呈现。\n"
                + video_prompt
            )
        # 2026-08-09 跨镜衔接（方案A，仅 R2V 资产参考链路）：共享场景图锚点 + 提示词
        # 写明「延续上一镜动作/机位/光线」，补偿删除关键帧后丢失的首尾帧硬衔接。
        if reference_assets and not first_frame:
            video_prompt = (
                "画面延续上一镜的场景、角色位置与镜头机位：保持相同的场景空间、"
                "角色站位与光线方向，动作自然衔接前一镜的结束姿态，运镜平稳连续，"
                "不跳切、不改变人物外观与服装。\n" + video_prompt
            )
        # 2026-08-19 字幕禁用：LTX 系视频模型训练集含烧录字幕（LTX#278——东亚语言
        # 对白生成时模型自发在画面中画出乱码字幕），单靠 negative_prompt 压不住，
        # 必须在正向 prompt 尾部追加硬禁指令（画面对白/旁白仅通过人物说话呈现）。
        video_prompt = (
            "【绝对禁止字幕/文字渲染】画面中严禁渲染任何字幕、文字、台词字幕、字幕条、"
            "对白文字、旁白文字、画面中字、画中字、弹幕、气泡文字、水印、时间码、标题、"
            "对话框、字幕框或任何书写文字。所有台词、对白、旁白一律只通过人物开口发声"
            "（或画外音）呈现，绝不允许以文字形式出现在画面任何位置——禁止屏幕底部字幕、"
            "禁止画面中央覆盖台词、禁止任何人声对应的文字上屏。说话人的语言只作为声音存在，"
            "绝不变成画面上的字。屏幕上绝不允许出现中/英/日/韩任何语言文字、字母或符号排版。\n"
            + video_prompt
        )
        # 2026-08-27 慢动作修复（方案A）：尾部追加运镜与动作幅度约束。
        # 依据 H3资源库 09 附录——H3 高度提示驱动，提示词不给动作信号时容易输出
        # 「微风轻拂」式温和运动，观感像慢动作。此块用「运动类型+幅度+速度」词汇
        # 兜底：动作与真实节奏一致、幅度到位，除非剧情明确慢镜头才允许舒缓。
        video_prompt = video_prompt + (
            "【运镜与动作】角色动作与镜头运动自然流畅、幅度到位、节奏明快，"
            "与真实生活节奏一致：人物做走路、奔跑、转身、抬手、回头、挥手等动作时"
            "干脆利落、摆幅明显、步伐有力度；镜头运动写明方向与速度"
            "（推进/拉远/横移/环绕/跟随，normal 至 fast speed）；"
            "除剧情明确要求的慢镜头外，整体不得出现动作呆滞、迟缓、拖沓"
            "或近似静止的画面，禁止慢动作效果。\n"
        )
        # 负面词兜底：追加面部扭曲负面词，强化对人物面容崩坏的抑制（覆盖缓存旧值）
        if provider.provider_type == "http_poll":
            face_neg = (
                "warped face, distorted face, deformed face, facial distortion, "
                "face morphing, melting face, flickering face, unstable face, "
                "disfigured face, cross-eyed, misplaced facial features"
            )
            negative_prompt = f"{negative_prompt}, {face_neg}".strip(", ")
        # 2026-09-01 字幕负面词兜底：烧录字幕（对白/旁白/文字上屏）一律压入负面词，
        # 与正向「绝对禁止字幕」块 + 对白不上屏约束协同，双管齐下防模型渲染字幕。
        sub_neg = (
            "subtitles, on-screen subtitles, burned-in subtitles, subtitle text, "
            "captions, caption text, closed captions, dialogue text overlay, "
            "text overlay, subtitled text, text on screen, on-screen text, "
            "written dialogue, lyric text, watermark, timestamp, timestamp text, "
            "subtitle bar, text box, caption box, chinese subtitles, "
            "scrolling text, letterbox titles"
        )
        negative_prompt = f"{negative_prompt}, {sub_neg}".strip(", ")
        # R2V 参考图链路追加负面词：抑制参考图入画（四视图/缩略图/网格/照片）与多角色乱入
        if reference_assets:
            ref_neg = (
                "reference image, character sheet, four-view grid, turnaround sheet, "
                "thumbnail, photo frame, image collage, screenshot, UI overlay, "
                "picture-in-picture, photo on screen, multiple people, extra characters, "
                "crowd, bystanders, duplicate character, character lineup"
            )
            negative_prompt = f"{negative_prompt}, {ref_neg}".strip(", ")
        # 2026-08-22 可配置生成参数：分镜级 gen_params（三列工作台右侧面板）优先，
        # 未显式设置时回退项目级 video_params（项目详情/创建项目统一配置），再到默认。
        # None = 模型默认。注：steps 默认 None 由能力表决定（turbo LoRA 统一 8 步音频
        # 才收敛），仅当用户/项目显式设置时覆盖。
        gp = (segment.gen_params or {}) if segment else {}
        pgp = ((project.video_params or {}) if project else {}) or {}

        def _pick(*keys):
            """按 key 列表取第一个非空值（分镜级 → 项目级）。"""
            for k in keys:
                v = gp.get(k)
                if v not in (None, ""):
                    return v
                v = pgp.get(k)
                if v not in (None, ""):
                    return v
            return None

        _cfg_raw = _pick("cfg")
        _seed_raw = _pick("seed")
        try:
            _cfg = float(_cfg_raw) if _cfg_raw not in (None, "") else None
        except (TypeError, ValueError):
            _cfg = None
        try:
            _seed = int(_seed_raw) if _seed_raw not in (None, "") else None
        except (TypeError, ValueError):
            _seed = None
        _steps_raw = _pick("steps")
        try:
            _steps = int(_steps_raw) if _steps_raw not in (None, "") else None
        except (TypeError, ValueError):
            _steps = None
        # 2026-08-23：Turbo 档位 → SigmaShift 双流 shift + 步数预设（显式 steps 优先）
        _turbo = str(_pick("turbo") or "").lower()
        _turbo_preset = {
            "high": (9.0, 2.0, 8),
            "mid": (12.0, 3.0, 10),
            "low": (16.0, 4.0, 14),
        }.get(_turbo)
        _shift_video = style_params.get("shift_video")
        # 2026-08-27：shift_audio 已从风格档剥离（音频收敛是模型+步数属性），
        # 显式置 None → comfyui.py 回退 capability 铁律（FL2V=6.0 / R2V=3.0）。
        # Turbo 档位显式传入的 shift_audio 预设不受影响。
        _shift_audio = None
        if _turbo_preset:
            _shift_video, _shift_audio = _turbo_preset[0], _turbo_preset[1]
            if _steps is None:
                _steps = _turbo_preset[2]
        # 2026-08-23：帧率（分镜面板 fps → 项目级 fps → 默认）→ ComfyUI FPS
        _fps_raw = _pick("fps")
        try:
            _fps = int(_fps_raw) if _fps_raw not in (None, "") else int(clip.frame_rate or 24)
        except (TypeError, ValueError):
            _fps = int(clip.frame_rate or 24) or 24
        _fps = _fps if _fps > 0 else 24
        # 2026-09-01 原生音轨策略（用户拍板）：所有镜头（含纯画面/无对白）一律保留 H3 原生音频
        # ——保证"视频原生生成音频"。代价：纯画面镜头可能夹杂失真人声（用户已接受）。
        # 仍可用 分镜级/项目级 gen_params 的 native_audio 显式覆盖（false=本镜静音）。
        _na_raw = _pick("native_audio")
        if _na_raw in (None, ""):
            native_audio = True
        else:
            native_audio = str(_na_raw).strip().lower() in ("1", "true", "yes", "on")
        # 2026-09 修复「首帧与提示词不匹配致画面错乱」：prev_tail 硬首帧=上一镜尾帧，
        # 若本镜提示词开端情绪/景别与首帧不同，模型会把两种内容硬拼 → 面部扭曲/画面突变。
        # 在提示词最前（权重最高）声明「承接上一镜、以首帧为准、平滑过渡」，压制冲突。
        if ref_src == "prev_tail":
            video_prompt = build_prev_tail_continuity(prev_seg_desc) + "\n" + video_prompt
        opts = VideoOpts(
            prompt=video_prompt,
            width=clip.width, height=clip.height,
            num_frames=clip.num_frames, frame_rate=_fps,
            duration=clip.num_frames / _fps,
            negative_prompt=negative_prompt,
            shift_video=_shift_video,
            shift_audio=_shift_audio,
            steps=_steps,
            cfg=_cfg,
            seed=_seed,
            native_audio=native_audio,
        )
        handle = provider.imageToVideo(
            first_frame, clip.last_frame_url, opts,
            reference_assets=reference_assets,
        )
        update_task(
            db, task_id, provider=handle.provider,
            provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl, progress=10,
        )

        result = run_with_polling(
            db, task_id, provider, handle,
            poll_interval=settings.celery_video_poll_interval,
            timeout=settings.celery_video_timeout,
        )
        if not result.videoUrl:
            raise ProviderError(
                f"视频任务完成但未返回视频 URL（result_jsonpath 未匹配到值）。原始响应: {result.raw}"
            )
        local_url = download_to_local(
            result.videoUrl, subdir=f"videos/{clip.id}", filename="clip.mp4",
            task_id=task_id,
        )
        # 2026-08-10：MiniMax H3 开头自带 ~0.1s 瞬态爆音 → 音频淡入消除
        #（只重编码音频，视频流 copy，不影响生成流程）
        # 注意：settings 用模块级导入（第 12 行），不能在函数内重复 import——
        # 否则 Python 视 settings 为局部变量，函数体前段 run_with_polling 的
        # settings.celery_video_poll_interval 会报 UnboundLocalError。
        from app.utils.media import apply_audio_fade_in
        _local = os.path.join(settings.media_dir, local_url.split("/static/media/", 1)[1])
        apply_audio_fade_in(_local)
        clip.video_url = local_url
        clip.duration = result.duration or (clip.num_frames / clip.frame_rate)
        clip.status = MediaStatus.succeeded
        logger.info(
            "[video] 视频生成成功并落盘 clip_id=%s 本地URL=%s 远端=%s 时长=%.2fs",
            clip.id, local_url, result.videoUrl, clip.duration or 0,
        )
        if kf:
            kf.used_as_video_first_frame = True
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=local_url, finished_at=now(),
        )
        db.commit()
    except TaskCancelledError:
        # 用户取消/项目删除：不回写 failed，保持 cancelled（媒体状态已由 task_service 回退 pending）
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if target_id:
            clip = db.get(VideoClip, target_id)
            if clip:
                clip.status = MediaStatus.failed
                clip.error = msg
                db.commit()
        # 2026-08-10 对接服务器：任务已提交 ComfyUI（provider_task_id 已落库）
        # 后 worker 异常退出时，ComfyUI 可能仍在生成甚至已生成完毕。派发恢复
        # 任务，由它轮询 ComfyUI，完成后自动下载落库，避免"服务器生成了但
        # 系统端丢失"。用户主动取消（TaskCancelledError）不走此分支。
        t = db.get(Task, task_id)
        if t is not None and t.provider_task_id:
            try:
                from app.tasks.recover_orphan_video import recover_orphan_video

                recover_orphan_video.delay(str(task_id))
                logger.info("[video] 已派发孤儿视频恢复 task_id=%s prompt=%s",
                            task_id, t.provider_task_id)
            except Exception:
                logger.exception("[video] 派发恢复任务失败 task_id=%s", task_id)
    finally:
        db.close()
