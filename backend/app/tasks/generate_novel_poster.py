"""剧本海报生成任务：写剧本完成后自动为剧本生成「电影海报」（文生图），写入 novel.poster_url。

2026-08-27 升级（贴合剧本 + 中文准确）：
1. 贴合剧本：poster prompt 不再带 "Story:/Plot:" 文字标签（实测会被生图模型画进画面），
   改为「类型档位（古装/悬疑/都市情感）+ 干净梗概」自然语言段；类型由标题+梗概启发式推断，
   画面须直接反映剧本情节，禁止自由发挥成无关题材。
2. 中文准确：生图模型画中文必然乱码（伪汉字），正解是「画面无字 + 代码合成标题」——
   生成后用 PIL + 系统中文字体（STHeiti/Hiragino）在 2:3 海报底部叠加准确中文剧名
   （底部渐变遮罩保证可读，自适应字号，最多两行）。找不到字体/失败时静默跳过不阻断。
3. 可重生成：支持 force=True（覆盖旧海报 + 删除旧文件），前端「重新生成海报」按钮走此。
"""
import logging
import os
import re

from app.config import settings
from app.database import SessionLocal
from app.models.model_config import Model, ModelType
from app.models.task import Task, TaskStatus, TaskType
from app.providers.base import ImageOpts
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.providers.errors import map_to_chinese as _map_to_chinese
from app.tasks.base import download_to_local, now, run_with_polling, update_task
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_CJK = re.compile("[\u4e00-\u9fff]+")

# 系统中文字体候选（macOS 优先；找不到则跳过标题合成，不阻断海报生成）
_FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

# 文生图负面词：双保险压制画面文字/伪汉字
_NEGATIVE_TEXT = "text, letters, Chinese characters, Japanese characters, Korean characters, gibberish glyphs, pseudo text, slogan, title, subtitle, watermark, logo, UI, icon, lowres, blurry"


def _clean_text(s):
    """剥离中文字符（避免生图模型把中文当画面文字渲染）并压缩空白。"""
    s = _CJK.sub(" ", s or "")
    s = re.sub(r"[，。；：？！、·…—‘’“”《》【】（）\u3000,;:!?()]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:400]


def _detect_genre(title: str, synopsis: str) -> str:
    """从原始（未清洗）标题+梗概启发式判断题材档位，用于海报风格方向。

    启发式必然有噪声，宁可保守：抓不准一律回退「现代都市情感」。
    """
    t = f"{title or ''} {synopsis or ''}"
    if any(k in t for k in ("古装", "宫斗", "王爷", "将军", "修仙", "玄幻", "仙侠", "侠客", "穿越")):
        return "ancient Chinese costume epic"
    if any(k in t for k in ("黑道", "卧底", "凶杀", "碎尸", "连环", "庭审", "侦探", "背叛复仇")):
        return "dark suspense thriller noir"
    if any(k in t for k in ("刑侦", "专案", "追凶", "警队", "罪案")):
        return "crime investigation drama"
    if any(k in t for k in ("霸道", "总裁", "甜宠", "相亲", "婚礼", "离婚", "彩礼", "婚约", "三角恋")):
        return "modern urban romantic drama"
    return "modern urban emotional realistic drama"


def _to_local_path(url: str) -> str:
    """把静态 media URL（http://…/static/media/…）还原成本地文件路径；
    本来就是本地路径则原样返回。"""
    if "/static/media/" in (url or ""):
        return os.path.join(settings.media_dir, url.split("/static/media/", 1)[1])
    return url


def _normalize_poster_2x3(local_path: str) -> str:
    """强制海报为精确 2:3 竖版（2026-08-24）。

    生成端已按 ratio=2:3 请求（ComfyUI _size → 768×1152 竖版），但个别
    provider/权重可能把尺寸归一到邻近档位（如 1:1 方形、4:5），缩略图与
    渲染都会裁切内容。这里用 Pillow 做中心缩放+裁剪的 cover 归一：
    - 太宽（4:3 等）→ 按高裁宽到 2:3
    - 太方/太横（1:1）→ 先等比放大到目标高，再裁宽到 2:3
    始终保持 16 倍数（VAE/解码对齐），失败时原样返回（不阻断主流程）。
    """
    from PIL import Image

    out = local_path
    try:
        img = Image.open(local_path)
        w, h = img.size
        ratio_w, ratio_h = 2, 3
        cur = w / h
        target = ratio_w / ratio_h
        if abs(cur - target) > 0.02:  # 宽容差：几乎 2:3 不折腾
            if cur > target:
                nw = int(round(h * target / 16) * 16)
                nw = min(nw, w)
                left = (w - nw) // 2
                img = img.crop((left, 0, left + nw, h))
            else:
                nh = max(int(round(w / target / 16) * 16), h)
                scale = nh / h
                img = img.resize((max(1, int(round(w * scale))), nh), Image.LANCZOS)
                w2 = max(1, int(round(nh * target / 16) * 16))
                left = (img.width - w2) // 2
                img = img.crop((left, 0, left + w2, nh))
            out = local_path.rsplit(".", 1)[0] + "_2x3.png"
            img.convert("RGB").save(out, "PNG")
    except Exception:  # noqa: BLE001 后处理失败不阻断海报生成
        logger.warning("海报 2:3 归一失败（保留原图）: %s", local_path)
        out = local_path
    return out


def _compose_cn_title(local_path: str, title: str) -> str:
    """在 2:3 海报底部合成准确中文剧名（代码渲染，杜绝生图伪汉字乱码）。

    版式：底部竖向渐变遮罩（可读性）→ 中文标题（自适应字号，最多两行，
    每行宽度 ≤ 88% 画宽，左下对齐）→ 标题上方细白线点缀。
    找不到中文字体 / 标题为空 / 任一步失败 → 原样返回（不阻断海报链路）。
    """
    title = re.sub(r"\s+", "", title or "").strip()
    if not title:
        return local_path
    font_path = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)
    if not font_path:
        logger.warning("未找到中文字体，跳过海报标题合成: %s", local_path)
        return local_path
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(local_path).convert("RGBA")
        W, H = img.size

        # 1) 底部渐变遮罩（50% 高度起渐深，提高白字可读性）
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        top = int(H * 0.5)
        for y in range(top, H):
            a = int(150 * ((y - top) / max(1, H - top))) if H > top else 0
            od.line([(0, y), (W, y)], fill=(6, 8, 14, a))
        img = Image.alpha_composite(img, overlay)
        draw = ImageDraw.Draw(img)

        # 2) 标题分行 + 自适应字号
        if len(title) <= 8:
            lines = [title]
        else:
            half = (len(title) + 1) // 2
            lines = [title[:half], title[half:]]
        max_w = int(W * 0.88)
        size = int(H * 0.075)
        while size > 14:
            try:
                fnt = ImageFont.truetype(font_path, size)
            except Exception:  # noqa: BLE001
                break
            if max(draw.textlength(ln, font=fnt) for ln in lines) <= max_w:
                break
            size -= 2
        try:
            fnt = ImageFont.truetype(font_path, max(size, 14))
        except Exception:  # noqa: BLE001
            return local_path

        # 3) 绘制：细白线 + 阴影 + 白色标题，从下往上排
        pad_x = int(W * 0.06)
        pad_bottom = int(H * 0.05)
        y = H - pad_bottom - size
        line_gap = int(size * 1.32)
        accent_w = int(W * 0.10)
        for idx, ln in enumerate(lines):
            yy = y - (len(lines) - 1 - idx) * line_gap
            if idx == len(lines) - 1:  # 末行上方放细白线
                draw.line(
                    [(pad_x, yy - int(H * 0.018)), (pad_x + accent_w, yy - int(H * 0.018))],
                    fill=(255, 255, 255, 235), width=max(2, int(H * 0.004)),
                )
            draw.text((pad_x + 2, yy + 2), ln, font=fnt, fill=(0, 0, 0, 170))
            draw.text((pad_x, yy), ln, font=fnt, fill=(255, 255, 255, 255))

        out = local_path.rsplit(".", 1)[0] + "_final.png"
        img.convert("RGB").save(out, "PNG")
        return out
    except Exception as e:  # noqa: BLE001 合成失败不阻断海报链路
        logger.warning("海报中文标题合成失败（保留原图）: %s", e)
        return local_path


def _poster_english_direction(novel, db) -> str:
    """用文本模型把剧本压缩成一段英文「海报画面方向」（场景/人物/服饰/情绪/氛围）。

    生图模型读不懂中文，纯中文剧本清洗后没有可用内容 → 必须先把剧情翻译/压缩
    成英文视觉方向，海报才能贴合剧本。失败时返回 ""（调用方回退干净梗概）。
    """
    try:
        from app.models.model_config import ModelType as _MT
        from app.providers.registry import ProviderRegistry as _PR
        from app.services.keyframe_service import _resolve_model as _rm

        model = _rm(db, None, _MT.text, "script")
        provider = _PR.for_model_id(db, model.id)
        raw = ""
        outline = (novel.outline or {}).get("synopsis") if isinstance(novel.outline, dict) else None
        if outline:
            raw = str(outline)
        elif (novel.raw_text or "").strip():
            raw = novel.raw_text[:1000]
        if not raw.strip():
            return ""
        sys = "You are a movie poster art director. Output ONLY plain English, no markdown, no labels."
        usr = (
            "Based on this Chinese short-drama script, write 60-90 words of English visual direction "
            "for a movie poster: main character(s) appearance & costume, key setting, the single dramatic "
            "moment to depict, and mood/lighting. Use concrete visual nouns and action verbs, no dialogue, "
            "no plot summary, no text-on-poster suggestions.\n\nScript:\n" + raw[:1000]
        )
        resp = provider.chat([{"role": "system", "content": sys}, {"role": "user", "content": usr}])
        out = (resp["choices"][0]["message"]["content"] or "").strip()
        out = re.sub(r"\s+", " ", out)
        return out[:500]
    except Exception as e:  # noqa: BLE001 方向提取失败不阻断海报（回退梗概文本）
        logger.warning("海报英文方向提取失败（回退梗概文本）: %s", e)
        return ""


def _poster_prompt(novel, english_direction: str = "") -> str:
    """从原始剧本标题 + 梗概（或 LLM 英文画面方向）组装电影海报 prompt（英文，无文字标签，绑定剧情）。"""
    raw_synopsis = ""
    outline = (novel.outline or {}).get("synopsis") if isinstance(novel.outline, dict) else None
    if outline:
        raw_synopsis = str(outline)
    elif (novel.raw_text or "").strip():
        raw_synopsis = novel.raw_text[:600]
    title_clean = _clean_text(novel.title or "short drama")
    synopsis_clean = _clean_text(raw_synopsis)
    genre = _detect_genre(novel.title, raw_synopsis)

    parts = [
        f"A vertical 2:3 cinematic movie poster in {genre} style. Depict the core dramatic moment and the main character(s) exactly as described in the plot below: same setting, costume, mood and relationship clues. Stay true to the story; do not invent unrelated themes, actions or characters.",
        "Composition: main subject centered with emotional expressiveness, dramatic Rembrandt lighting, rich cinematic color grading, shallow depth of field, film-quality detail, subtle vignette.",
        "CRITICAL: absolutely no text of any kind in the image - no Chinese or other characters, no letters, numerals, words, slogans, subtitles, watermarks, logos or UI. The image must be pure imagery with no writing.",
    ]
    # 剧情主体：优先 LLM 提取的英文画面方向（贴合剧本），失败回退干净梗概
    plot_body = (english_direction or "").strip() or synopsis_clean
    if title_clean:
        parts.append("Setting: " + title_clean + ".")
    if plot_body:
        parts.append(plot_body)
    return "\n".join(parts)


@celery_app.task(name="generate_novel_poster", bind=True)
def generate_novel_poster_task(self, novel_id: str, model_id=None, force: bool = False) -> None:
    db = SessionLocal()
    task_id: str | None = None
    try:
        from app.models.novel import Novel

        novel = db.get(Novel, novel_id)
        if novel is None or not (novel.raw_text or "").strip():
            return
        if novel.poster_url and not force:
            return  # 幂等：已有海报跳过（force=True 时强制重生成）

        # 2026-08-27 修复：write_script 传来的是「文本模型 id」；文本模型的 openai_compatible
        # provider 没有 images/generations（发过去 400，ComfyUI 收不到海报任务）。
        # 只有 model_id 指向真正 image 模型时才用，否则回退到场景文生图模型（ComfyUI）。
        _explicit = db.get(Model, model_id) if model_id else None
        if _explicit is not None and _explicit.model_type != ModelType.image:
            _explicit = None
        model = _resolve_model(db, _explicit.id if _explicit else None, ModelType.image, "scene")
        provider = ProviderRegistry.for_model_id(db, model.id)

        if force and novel.poster_url:
            # 清旧海报文件 + 置空 URL：失败回滚时仍保留任务失败语义，不误伤剧本
            try:
                old = _to_local_path(novel.poster_url)
                if old and os.path.exists(old) and settings.media_dir in old:
                    os.remove(old)
            except Exception:  # noqa: BLE001
                pass
            novel.poster_url = None
            db.commit()

        task = Task(
            type=TaskType.generate_asset_cover,
            target_type="novel",
            target_id=novel.id,
            status=TaskStatus.pending,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = str(task.id)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        english_direction = _poster_english_direction(novel, db)
        prompt = _poster_prompt(novel, english_direction)
        opts = ImageOpts(
            ratio="2:3", size="2K",
            negative_prompt=_NEGATIVE_TEXT,
        )
        handle = provider.textToImage(prompt, opts)
        update_task(
            db, task_id, provider=handle.provider,
            provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl, progress=10,
        )
        result = run_with_polling(db, task_id, provider, handle, poll_interval=2, timeout=600)
        url0 = download_to_local(
            result.imageUrls[0], subdir="novels/" + str(novel.id), filename="poster.png",
            task_id=task_id,
        )
        local0 = _to_local_path(url0)
        local0 = _normalize_poster_2x3(local0)
        final_local = _compose_cn_title(local0, novel.title)
        fname = os.path.basename(final_local)
        novel.poster_url = f"{settings.static_base_url}/media/novels/{novel.id}/{fname}"
        db.commit()
        update_task(
            db, task_id, status=TaskStatus.succeeded,
            progress=100, result_url=novel.poster_url, finished_at=now(),
        )
        logger.info("剧本海报已生成(%s): %s", "重新生成" if force else "首次", novel.title)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        # 2026-08-27 修复：失败必须回写任务 failed（此前遗留 running → 被 reclaim 回收，
        # _mark_target_failed 把已写好的剧本误标 failed「任务被回收」）。
        if task_id:
            try:
                update_task(
                    db, task_id, status=TaskStatus.failed,
                    error=f"剧本海报生成失败: {_map_to_chinese(e)}", finished_at=now(),
                )
            except Exception:  # noqa: BLE001 - 回写失败不遮主异常
                pass
        logger.warning("剧本海报生成失败（不影响剧本/分镜产出）: %s", e)
    finally:
        db.close()
