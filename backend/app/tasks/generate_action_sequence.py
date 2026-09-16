"""白模故事版与多镜头动作展示任务（2026-08-10，手动触发）。

3 个任务：
1. generate_action_sequence_template（阶段1）：LLM 格子分组 → 图生图一张多格白模模板图
   → 落库 ActionSequence → 派发阶段2。
2. compose_action_sequence（阶段2）：模板图按 grid 裁剪格子图 → 逐组派发
   generate_action_video 子任务（格子图 R2V 姿态强参考 + 正式资产外观）→ 轮询
   → 硬切拼接成一条完整动作视频 → 回写 composed_url。
3. generate_action_video（子任务）：每组一个 5s 视频，首帧用组内分镜正式关键帧，
   格子图仅作姿态参考（外观隔离，画面保持正式风格）。

拼接产物（composed_url）由 export_film 幕级段优先使用，实现「替换幕级片段」。
"""
import json
import logging
import math
import os
import subprocess
import time
import uuid

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.action_sequence import ActionSequence
from app.models.asset import Asset, AssetType
from app.models.media import Keyframe, MediaStatus
from app.models.model_config import Model, ModelType
from app.models.project import Episode
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.providers.base import ImageOpts, VideoOpts
from app.providers.errors import (
    ProviderError,
    is_content_policy,
    is_rate_limit,
    map_to_chinese,
)
from app.providers.registry import ProviderRegistry
from app.services.action_sequence_service import (
    collect_sequence_segments,
    _grid_layout,
    list_by_episode,
    plan_template_groups,
)
from app.services.episode_video_service import _build_speech_block
from app.services.keyframe_service import _resolve_model, _select_view_for_shot
from app.services.style_service import get_effective_style_prompt, get_style_video_params
from app.tasks.base import (
    TaskCancelledError,
    download_to_local,
    now,
    run_with_polling,
    update_task,
)
from app.tasks.celery_app import celery_app
from app.tasks.generate_export import (
    compose_segment_multi,
    concat_clips_xfade,
    ffprobe_size,
    url_to_local_path,
)
from app.services.video_service import dims_for_ratio

logger = logging.getLogger(__name__)

_POLICY_MAX_RETRIES = 3
_RATE_LIMIT_MAX_RETRIES = 3
# 拼接轮询：视频生成较慢，5s 一次，最长 90 分钟
_COMPOSE_POLL_INTERVAL = 5
_COMPOSE_TIMEOUT = 5400

# 白模模板图负面词：低质/文字/水印/多人/贴图/彩色（模板图必须灰模单色）
_TEMPLATE_NEGATIVES = (
    "low quality, lowres, blurry, watermark, text, subtitle, captions, label, "
    "logo, signature, colored, color, texture, detailed texture, multiple people, "
    "extra person, deformed, distorted, bad anatomy, bad hands, cropped, "
    "out of frame, cluttered background, photorealistic skin, hair strands"
)

# 逐组视频负面词：防白模材质残留 + 画面文字 + 通用崩坏
_ACTION_VIDEO_NEGATIVES = (
    "low quality, lowres, blurry, watermark, text, subtitle, captions, logo, "
    "clay model, gray model, untextured, wireframe, 3d render, plastic figure, "
    "mannequin, statue, extra person, duplicate, warped face, distorted face, "
    "deformed face, facial distortion, face morphing, melting face, "
    "flickering face, unstable face, disfigured face, cross-eyed, "
    "misplaced facial features, oversmoothed skin, plastic skin, "
    "stiff motion, morphing artifacts, flickering, jitter"
)


def _load_seq(db, seq_id: str) -> ActionSequence:
    seq = db.get(ActionSequence, seq_id)
    if seq is None:
        raise ProviderError("动作序列不存在")
    return seq


def _asset_refs(db, segments: list[Segment], *, ref_max: int = 4) -> list[str]:
    """动作段资产参考图：场景 cover + 首个角色（主角）视图，≤ref_max 张。

    沿用幕级设计图教训（多图合成必崩）——模板图/逐组视频外观锚点只用
    场景 + 主角，其余角色/道具由 prompt 描述由模型自行生成。
    """
    refs: list[str] = []
    seen: set[str] = set()

    def _add(u):
        if u and u not in seen:
            seen.add(u)
            refs.append(u)

    scene_id = next((s.scene_id for s in segments if s.scene_id), None)
    if scene_id:
        try:
            a = db.get(Asset, uuid.UUID(str(scene_id)))
        except (ValueError, TypeError):
            a = None
        if a:
            _add(a.cover_url)
    for s in segments:
        if s.character_ids:
            try:
                a = db.get(Asset, uuid.UUID(str(s.character_ids[0])))
            except (ValueError, TypeError):
                a = None
            if a:
                view = _select_view_for_shot(a, s.shot_type, s.camera)
                _add(view or a.cover_url)
                break
    return refs[:ref_max]


# ───────────────────────── 阶段1：白模模板图 ─────────────────────────

def _build_template_prompt(plan: dict, segments: list[Segment], style: str | None) -> str:
    """白模模板图 prompt：灰模风格 + 多格布局 + 每格动作节拍。"""
    rows, cols = _grid_layout(plan["grid_count"])
    cells_lines = []
    for c in plan["cells"]:
        cells_lines.append(
            f"Panel {c['index']}: {c['beat']} - {c['pose'] or 'dynamic pose'}"
        )
    cells_block = "\n".join(cells_lines)
    style_line = f"Art style: {style}" if style else "Art style: 写实电影风格"
    # 图生图多图参考：资产参考图保持角色/场景外观锚点，画面风格灰模化
    return (
        f"A storyboard template sheet divided into a {rows}x{cols} grid of "
        f"{plan['grid_count']} panels, showing a continuous action fight sequence. "
        "All panels use the same character and scene from the reference images, "
        "rendered as untextured white-gray clay models (3D previs gray model style). "
        "Each panel shows one action beat of the fight, chronological from top-left "
        "to bottom-right:\n"
        f"{cells_block}\n"
        "Keep consistent character pose and camera angle within each panel. "
        "Plain light gray background, soft studio lighting, clean simple look. "
        "No text, no labels, no numbers, no arrows, no color, no texture details, "
        "no outlines. Composition: each panel is a separate storyboard cell, "
        "evenly spaced with clear separation.\n"
        f"{style_line}"
    )


def _generate_template_image(
    db, task_id, seq_id: str, prompt: str, refs: list[str],
    ratio: str, negative: str,
) -> str:
    """图生图生成一张多格白模模板图（有参考图走 img2img，无则文生图），返回本地 URL。"""
    model = _resolve_model(db, None, ModelType.image, "img2img" if refs else "keyframe")
    provider = ProviderRegistry.for_model(model)
    opts = ImageOpts(ratio=ratio, size="2K", negative_prompt=negative)
    handle = None
    attempt = 0
    rate_attempt = 0
    while True:
        try:
            handle = (
                provider.imageToImage(prompt, refs, opts) if refs
                else provider.textToImage(prompt, opts)
            )
            break
        except Exception as e:
            if is_content_policy(e) and attempt < _POLICY_MAX_RETRIES - 1:
                attempt += 1
                logger.warning(
                    "[action_seq] 模板图被安全策略拦截（第 %s/%s 次），重试中…",
                    attempt + 1, _POLICY_MAX_RETRIES,
                )
                time.sleep(3 * attempt)
                continue
            if is_rate_limit(e) and rate_attempt < _RATE_LIMIT_MAX_RETRIES - 1:
                rate_attempt += 1
                logger.warning(
                    "[action_seq] 模板图触发限流 429（第 %s/%s 次），%s 秒后重试…",
                    rate_attempt + 1, _RATE_LIMIT_MAX_RETRIES, 10 * rate_attempt,
                )
                time.sleep(10 * rate_attempt)
                continue
            raise
    result = run_with_polling(db, task_id, provider, handle, poll_interval=2, timeout=600)
    if not result.imageUrls:
        raise ProviderError("白模模板图完成但未返回图片 URL")
    return download_to_local(
        result.imageUrls[0], subdir=f"action_sequences/{seq_id}",
        filename="template.png", task_id=task_id,
    )


@celery_app.task(name="generate_action_sequence_template", bind=True)
def generate_action_sequence_template_task(self, task_id: str):
    """阶段1：LLM 格子分组 → 生成白模模板图 → 落库 → 派发阶段2。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        seq = _load_seq(db, str(task.target_id))
        seq_id = str(seq.id)
        episode = db.get(Episode, seq.episode_id)
        project = episode.project if episode else None
        ratio = project.aspect_ratio if project else "16:9"

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        seq.status = MediaStatus.running
        seq.error = None
        db.commit()

        segments = collect_sequence_segments(db, seq.episode_id, seq.sequence_key)
        if not segments:
            raise ProviderError(f"幕内没有标记为「{seq.sequence_key}」的动作分镜")

        # 1) LLM 格子分组
        plan = plan_template_groups(db, segments)
        # 2) 生成白模模板图
        style = get_effective_style_prompt(db, project) if project else None
        refs = _asset_refs(db, segments)
        prompt = _build_template_prompt(plan, segments, style)
        url = _generate_template_image(db, task_id, seq_id, prompt, refs, ratio, _TEMPLATE_NEGATIVES)

        # 落库：group 补 segment_ids（LLM 只回 segment_indexes，转成分镜 id 供视频首帧/台词解析）
        by_index = {s.index: s for s in segments}
        groups = []
        for g in plan["groups"]:
            seg_ids = [str(by_index[i].id) for i in g["segment_indexes"] if i in by_index]
            groups.append({**g, "segment_ids": seg_ids})
        seq.template_url = url
        seq.grid_count = plan["grid_count"]
        seq.groups = groups
        seq.videos_url = [None] * len(groups)
        db.commit()
        update_task(db, task_id, progress=30)
        logger.info(
            "[action_seq] 幕 %s 模板图生成成功 url=%s 格数=%d 组数=%d 参考图=%d 张",
            seq.episode_id, url, plan["grid_count"], len(plan["groups"]), len(refs),
        )

        # 3) 派发阶段2（逐组视频 + 拼接）
        comp_task = Task(
            project_id=(project.id if project else seq.episode.project_id),
            type=TaskType.compose_action_sequence,
            target_type="action_sequence",
            target_id=seq.id,
            model_id=task.model_id,
            status=TaskStatus.pending,
        )
        db.add(comp_task)
        db.commit()
        db.refresh(comp_task)
        from app.tasks.generate_action_sequence import compose_action_sequence_task

        compose_action_sequence_task.delay(str(comp_task.id))
        logger.info("[action_seq] 序列 %s 已派发阶段2 逐组视频任务 task=%s", seq_id, comp_task.id)

        update_task(db, task_id, status=TaskStatus.succeeded, progress=100, finished_at=now())
    except TaskCancelledError:
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        seq = db.get(ActionSequence, task.target_id) if task is not None else None
        if seq is not None:
            seq.status = MediaStatus.failed
            seq.error = msg
            db.commit()
    finally:
        db.close()


# ───────────────────────── 阶段2：逐组视频 + 拼接 ─────────────────────────

def _crop_cells(seq: ActionSequence) -> list[str]:
    """把模板图按 grid 布局裁剪成格子图，落盘 media 目录，返回可访问 URL 列表。

    格子按「从左到右、从上到下」编号 1..grid_count（与 LLM cells.index 对齐）。
    """
    local = url_to_local_path(seq.template_url)
    if not local or not os.path.exists(local):
        raise ProviderError(f"模板图文件缺失：{seq.template_url}")
    rows, cols = _grid_layout(seq.grid_count)
    w, h = ffprobe_size(local)
    cell_w, cell_h = w // cols, h // rows
    out_dir = os.path.join(settings.media_dir, f"action_sequences/{seq.id}/cells")
    os.makedirs(out_dir, exist_ok=True)
    urls: list[str] = []
    for k in range(1, seq.grid_count + 1):
        x = ((k - 1) % cols) * cell_w
        y = ((k - 1) // cols) * cell_h
        out = os.path.join(out_dir, f"cell_{k}.png")
        # 幂等：已裁剪且非空则跳过（compose 与子任务并发调用时避免重复裁剪）
        if not (os.path.exists(out) and os.path.getsize(out) > 0):
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", local,
                    "-vf", f"crop={cell_w}:{cell_h}:{x}:{y},scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    out,
                ],
                check=True, capture_output=True,
            )
        urls.append(f"{settings.static_base_url}/media/action_sequences/{seq.id}/cells/cell_{k}.png")
    return urls


def _group_first_frame(db, group: dict) -> str | None:
    """组视频首帧：组内首个分镜的最新成功关键帧（正式风格）。"""
    seg_ids = group.get("segment_ids") or []
    if not seg_ids:
        return None
    first_sid = seg_ids[0]
    try:
        first_uuid = uuid.UUID(str(first_sid))
    except (ValueError, TypeError):
        return None
    try:
        kf = db.scalar(
            select(Keyframe)
            .where(Keyframe.segment_id == first_uuid, Keyframe.status == MediaStatus.succeeded)
            .order_by(Keyframe.index.desc())
            .limit(1)
        )
    except (TypeError, ValueError):
        return None
    return kf.image_url if kf else None


def _build_action_video_prompt(db, seq: ActionSequence, group: dict, style: str | None) -> str:
    """逐组视频 prompt：组动作描述 + 姿态参考/外观隔离声明 + 台词原句 + 风格。"""
    segments = _group_segments(db, seq, group)
    action_desc = ""
    if segments:
        action_desc = group.get("prompt") or (segments[0].description or "").strip()
    parts = [
        "【动作】",
        action_desc or "连贯动作",
        "",
        "【姿态参考】参考多格模板图中对应格子的动作姿态与镜头构图，但忽略灰模材质与颜色，"
        "画面不得出现白模/灰模/未上色模型，保持正式渲染风格。",
        "【外观】角色外观、场景与道具以正式资产参考图为准，与分镜画面一致，不得改变。",
        "",
    ]
    speech = _build_speech_block(segments) if segments else ""
    if speech:
        parts += [
            "【语言要求】本组所有角色语音、对白、喝声必须且只能用简体中文（普通话，zh-CN）发出："
            "逐字朗读下面「」内的中文原句，绝对禁止翻译成英语或任何外语、禁止夹杂任何英文单词；"
            "台词一律通过人物说话发声呈现，严禁把台词渲染为画面字幕/气泡/文字。",
            "【台词原句】",
            speech,
            "",
        ]
    parts += [
        "【画质约束】4K 高清，电影质感，动作流畅自然、幅度到位、与真实节奏一致；"
        "人物面部结构稳定不变形、五官清晰；动作干脆利落，不呆滞、不迟缓、不得"
        "呈现慢动作效果（除剧情明确要求的慢镜头）；运镜稳定；禁止文字、字幕、水印、logo、边框。",
        style or "Art style: 写实电影风格",
    ]
    return "\n".join(parts)


def _group_segments(db, seq: ActionSequence, group: dict) -> list[Segment]:
    """组内分镜（按 seq.segment_ids 中顺序）。"""
    sid_set = set(str(s) for s in group.get("segment_ids") or [])
    by_id = {str(s): s for s in collect_sequence_segments(db, seq.episode_id, seq.sequence_key)}
    return [by_id[sid] for sid in seq.segment_ids if sid in sid_set and sid in by_id]


def _generate_action_video(db, task_id, seq: ActionSequence, group_index: int, cell_urls: list[str]):
    """单个 5s 动作视频：首帧正式关键帧 + 格子图/资产 R2V 姿态外观参考。"""
    seq_id = str(seq.id)
    group = seq.groups[group_index]
    episode = db.get(Episode, seq.episode_id)
    project = episode.project if episode else None
    # 逐组视频用「video」场景模型（模板图阶段是 image 模型，不能复用）
    model = _resolve_model(db, None, ModelType.video, "video")
    # 项目级视频分辨率（480p/720p）：覆盖 ComfyUI 视频模型档位，全项目统一
    provider = ProviderRegistry.for_model(
        model, resolution=(project.resolution if project else None)
    )

    segments = _group_segments(db, seq, group)
    first_frame = _group_first_frame(db, group)
    if not first_frame:
        raise ProviderError(
            "组内首个分镜缺少正式关键帧，请先为该分镜生成关键帧（动作序列视频需要正式首帧）"
        )
    style = get_effective_style_prompt(db, project) if project else None
    prompt = _build_action_video_prompt(db, seq, group, style)

    ratio = project.aspect_ratio if project else "16:9"
    width, height = dims_for_ratio(ratio, project.resolution if project else None)
    style_params = get_style_video_params(db, project)
    negative = _ACTION_VIDEO_NEGATIVES
    if style_params.get("negative_extra"):
        negative = f"{negative}, {style_params['negative_extra']}".strip(", ")

    # R2V 参考：格子图（姿态）+ 角色资产（外观），受 ref_max 上限约束
    reference_assets = list(cell_urls[:])
    for u in _asset_refs(db, segments, ref_max=2):
        if u and u not in reference_assets:
            reference_assets.append(u)
    cap = getattr(model, "capability", None) or {}
    ref_max = int(cap.get("ref_max", 8))
    reference_assets = reference_assets[:ref_max]

    opts = VideoOpts(
        prompt=prompt,
        width=width, height=height,
        num_frames=121, frame_rate=24,
        duration=121 / 24,
        negative_prompt=negative,
        shift_video=style_params.get("shift_video"),
        # shift_audio 已从风格档剥离（2026-08-27）：None → capability 铁律（FL2V=6.0 / R2V=3.0）
        shift_audio=None,
        steps=None,
    )
    handle = provider.imageToVideo(
        first_frame, None, opts, reference_assets=reference_assets,
    )
    update_task(
        db, task_id, provider=handle.provider,
        provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl,
        progress=10,
    )
    result = run_with_polling(
        db, task_id, provider, handle,
        poll_interval=settings.celery_video_poll_interval,
        timeout=settings.celery_video_timeout,
    )
    if not result.videoUrl:
        raise ProviderError("动作视频任务完成但未返回视频 URL")
    local_url = download_to_local(
        result.videoUrl, subdir=f"action_sequences/{seq_id}/videos",
        filename=f"group_{group_index + 1}.mp4", task_id=task_id,
    )
    from app.utils.media import apply_audio_fade_in

    _local = url_to_local_path(local_url)
    if _local:
        apply_audio_fade_in(_local)
    return local_url, result.duration or (121 / 24)



@celery_app.task(name="generate_action_video", bind=True)
def generate_action_video_task(self, task_id: str, seq_id: str, group_index: int):
    """子任务：生成一组（1 个 5s）动作视频，落库 seq.videos_url[group_index]。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        seq = _load_seq(db, seq_id)
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        cell_urls = _crop_cells(seq)  # 幂等：已存在则跳过重裁
        url, dur = _generate_action_video(db, task_id, seq, group_index, cell_urls)
        seq = _load_seq(db, seq_id)
        videos = list(seq.videos_url or [])
        while len(videos) <= group_index:
            videos.append(None)
        videos[group_index] = url
        seq.videos_url = videos
        db.commit()
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=url, finished_at=now(),
        )
        logger.info(
            "[action_seq] 序列 %s 第 %d 组动作视频生成成功 url=%s 时长=%.2fs",
            seq_id, group_index + 1, url, dur,
        )
    except TaskCancelledError:
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
    finally:
        db.close()


@celery_app.task(name="compose_action_sequence", bind=True)
def compose_action_sequence_task(self, task_id: str):
    """阶段2：裁剪格子 → 逐组派发子任务 → 轮询 → 硬切拼接 → 回写 composed_url。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        seq = _load_seq(db, str(task.target_id))
        seq_id = str(seq.id)
        groups = seq.groups or []
        if not groups:
            update_task(db, task_id, status=TaskStatus.failed, error="格子分组为空", finished_at=now())
            return

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        seq.status = MediaStatus.running
        db.commit()

        # 1) 裁剪格子图（子任务内幂等重裁）
        _crop_cells(seq)

        # 2) 派发子任务
        sub_ids: list[str] = []
        for gi in range(len(groups)):
            sub = Task(
                project_id=task.project_id,
                type=TaskType.compose_action_sequence,
                target_type="action_sequence",
                target_id=seq.id,
                model_id=task.model_id,
                status=TaskStatus.pending,
            )
            db.add(sub)
            db.flush()
            sub_ids.append(str(sub.id))
        task.provider_task_id = json.dumps(sub_ids)
        db.commit()
        from app.tasks.generate_action_sequence import generate_action_video_task

        for gi, sid in enumerate(sub_ids):
            generate_action_video_task.delay(sid, seq_id, gi)

        # 3) 轮询子任务
        start = time.time()
        while time.time() - start < _COMPOSE_TIMEOUT:
            cur = db.get(Task, task_id)
            if cur is None or cur.status == TaskStatus.cancelled:
                return
            succeeded = failed = running = 0
            for sid in sub_ids:
                st = db.get(Task, sid)
                if st is None:
                    failed += 1
                elif st.status == TaskStatus.succeeded:
                    succeeded += 1
                elif st.status == TaskStatus.failed:
                    failed += 1
                else:
                    running += 1
            progress = 10 + int((succeeded + failed) / len(sub_ids) * 60)
            update_task(db, task_id, progress=progress, last_heartbeat_at=now())
            if running == 0:
                break
            time.sleep(_COMPOSE_POLL_INTERVAL)

        if (db.get(Task, task_id) or Task(status=TaskStatus.failed)).status == TaskStatus.cancelled:
            return

        # 4) 拼接：收集成功视频 → 统一规格 → 硬切（0.1s 短 crossfade 防爆音）
        seq = _load_seq(db, seq_id)
        videos = list(seq.videos_url or [])
        ok_urls = []
        for gi, sid in enumerate(sub_ids):
            st = db.get(Task, sid)
            if st is not None and st.status == TaskStatus.succeeded and gi < len(videos) and videos[gi]:
                ok_urls.append((gi, videos[gi]))
        if len(ok_urls) < len(sub_ids):
            raise ProviderError(
                f"动作视频部分失败（{len(ok_urls)}/{len(sub_ids)} 成功），请重新触发生成"
            )
        ok_urls.sort(key=lambda x: x[0])
        local_paths = [url_to_local_path(u) for _, u in ok_urls]
        if any(not p or not os.path.exists(p) for p in local_paths):
            raise ProviderError("动作视频文件缺失，无法拼接")

        episode = db.get(Episode, seq.episode_id)
        project = episode.project if episode else None
        ratio = project.aspect_ratio if project else "16:9"
        width, height = dims_for_ratio(ratio, project.resolution if project else None)

        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="action_seq_")
        composed_paths: list[str] = []
        for i, p in enumerate(local_paths):
            out = os.path.join(tmpdir, f"clip_{i}.mp4")
            compose_segment_multi(
                p, [], out, include_voice=False,
                tmpdir=tmpdir, target_w=width, target_h=height, fps=24,
            )
            composed_paths.append(out)
        concat_path = os.path.join(tmpdir, "composed.mp4")
        n = len(composed_paths)
        total_dur = concat_clips_xfade(
            composed_paths, concat_path, fade_secs=[0.1] * (n - 1),
        )

        # 落盘到 media 目录（可访问 URL）
        out_dir = os.path.join(settings.media_dir, f"action_sequences/{seq_id}")
        os.makedirs(out_dir, exist_ok=True)
        final_path = os.path.join(out_dir, "composed.mp4")
        subprocess.run(["ffmpeg", "-y", "-i", concat_path, "-c", "copy", final_path],
                       check=True, capture_output=True)
        composed_url = f"{settings.static_base_url}/media/action_sequences/{seq_id}/composed.mp4"
        seq.composed_url = composed_url
        seq.composed_duration = total_dur
        seq.status = MediaStatus.succeeded
        seq.error = None
        db.commit()
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=composed_url, finished_at=now(),
        )
        logger.info(
            "[action_seq] 序列 %s 拼接完成 url=%s 时长=%.2fs 片段=%d",
            seq_id, composed_url, total_dur, n,
        )
    except TaskCancelledError:
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        seq = db.get(ActionSequence, task.target_id) if task is not None else None
        if seq is not None:
            seq.status = MediaStatus.failed
            seq.error = msg
            db.commit()
    finally:
        db.close()
