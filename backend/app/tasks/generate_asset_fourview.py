"""角色四视图生成任务。

两条链路（按模型 capability.ref_engine 分流）：
- **新链路（flux2_9b_charsheet，2026-08-09）**：封面驱动 FLUX.2 Klein 9B + CharacterSheet
  LoRA 生成**单张四格合一四视图**（左半身特征格不裁切 + 正面/侧面/背面全身，1536×1024），
  存 asset.character_sheet_url（R2V 参考图规格）。生成后 flood-fill 统一底色为封面实测
  底色（四格背景由模型随机产出，肉眼可见深浅差 → 后处理消除拼接感）。
- **旧链路（Z-Image turbo img2img）**：保留兼容——四张独立分图（正面=封面、
  侧面/背面/特写），存 four_view_urls。历史数据不受影响。

要求（2026-08-06 用户拍板，2026-08-07/09 更新）：
- 角色一致性：四格同一人，面容/发型/服装/配色与封面 100% 一致
- 底色与封面保持一致（封面实际底色 ≈RGB(233,234,233) 浅灰白，后处理统一）
- 特征格半身不裁切（腰部以上，非脸部特写）；正面/侧面/背面全身（三格不动）
"""
import logging
import re
import time

from app.database import SessionLocal
from app.models.asset import Asset
from app.models.media import MediaStatus
from app.models.model_config import Model
from app.models.task import Task, TaskStatus
from app.providers.base import ImageOpts
from app.providers.errors import is_content_policy, map_to_chinese
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

# 审核拦截重试：最多 3 次（间隔 3s→6s），与封面链路一致。
_POLICY_MAX_RETRIES = 3

# ─── CharacterSheet 新链路（flux2_9b_charsheet）───────────────────────────
# 四视图触发词（2026-08-09 验证通过）：第一格半身特征格（腰部以上不裁切），
# 其余三格正面/侧面/背面全身；同一人；底色与参考图（封面）完全一致。
# 2026-08-09 修复：非写实风格（动漫等）下 "Photorealistic" 与风格矛盾 → 拼接
# prompt 时按 is_realistic_style 切换 _CHARSHEET_PREFIX_ANIME 措辞。
# 2026-08-09 规则（适用于所有风格）：四视图底色统一纯白，与封面白底一致。
_CHARSHEET_PREFIX = (
    "Convert the character in the image to a Character Sheet showing a half-body "
    "portrait, front, side and back full body views. "
    "The first panel is a half-body portrait from the waist up, uncropped, showing "
    "the head, torso and arms, the same character as the reference image. "
    "The remaining three panels are front, side and back full body views, same "
    "character, identical face, hairstyle, outfit and colors. "
    "All panels on a light grey background, solid light grey backdrop, "
    "clean light grey #EBEBEB background, no background, no scenery. "
    "Photorealistic, consistent character across all panels, no distortion."
)
_CHARSHEET_PREFIX_ANIME = (
    "Convert the character in the image to a Character Sheet showing a half-body "
    "portrait, front, side and back full body views. "
    "The first panel is a half-body portrait from the waist up, uncropped, showing "
    "the head, torso and arms, the same character as the reference image. "
    "The remaining three panels are front, side and back full body views, same "
    "character, identical face, hairstyle, outfit and colors. "
    "All panels on a light grey background, solid light grey backdrop, "
    "clean light grey #EBEBEB background, no background, no scenery. "
    "Clean anime style, consistent character across all panels, no distortion."
)
# 2026-08-11：3D CG 类风格（3D半写实/3D渲染）四视图前缀。
# 此前被 is_realistic_style 误判为非写实 → 走 _CHARSHEET_PREFIX_ANIME
# "Clean anime style" 措辞 → 四视图生成成动漫风。3D 类须用 3D CG render 措辞，
# 否则风格片段（Art style: ...）无载体，模型无法输出 3D 半写实质感。
_CHARSHEET_PREFIX_3D = (
    "Convert the character in the image to a 3D CG Character Sheet showing a "
    "half-body portrait, front, side and back full body views. "
    "The first panel is a half-body portrait from the waist up, uncropped, showing "
    "the head, torso and arms, the same character as the reference image. "
    "The remaining three panels are front, side and back full body views, same "
    "character, identical face, hairstyle, outfit and colors. "
    "All panels on a light grey background, solid light grey backdrop, "
    "clean light grey #EBEBEB background, no background, no scenery. "
    "High quality 3D CG character render, sculpted 3D model, realistic proportions, "
    "3D facial features, consistent character across all panels, no distortion."
)

# 旧链路侧面/背面/特写独立生成 prompt（放 prompt 开头，权重最大）：
# - 全身约束：完整 head to toe + 双脚可见 + 人物只占画面中部留边距（防裁脚，
#   封面 1:1 全身图实测有效；设定卡 2x2 网格因空间不足无法做到）
# - 底色约束（踩坑 2026-08-06 实测）：封面底色是"浅灰白/米白"而非纯白，
#   之前强制 "plain solid white background" 会把侧面/背面拉成纯白(254-255)，
#   与封面(228-252)明显不一致 → 移除白色强制词，只写"与参考图底色完全一致"，
#   让 img2img 直接继承封面实际底色
# - 不用 "character design sheet" 术语（踩坑③：img2img 下会动漫化），
#   用 "photorealistic photograph" 写实表述
# - 视角强制（2026-08-07 对照实验定档）：
#   Z-Image Turbo img2img 跳变特性：denoise≤0.85 完全保持参考图（角度/构图指令
#   全失效，输出≈封面正面全身），≥0.9 才允许重绘实现角度变换 → 统一 denoise 0.9；
#   视角措辞改为"身体/头部朝特定方向转动"的明确动作描述，约束模型严格转角度
# - 2026-08-07 按行业四视图规范（Ref2VA 教学）调整：
#   · 侧面从 90° 纯正侧改为 45° 3/4 侧身（教学："不用纯 90° 正侧，45° 过渡侧视图
#     适配 90% 剧情镜头"；纯正侧在转场时人脸崩坏风险高）
#   · 特写从"封面裁头部横条"改为 img2img 胸像（教学："仅头部+肩膀、放大五官"，
#     是 Ref2VA 优先读取的锁脸核心视图；横条裁图无法作为锁脸参考），
#     与侧面/背面统一 1:1 正方形 1024×1024
# 注意：Agnes 审核对 "standing pose" 误判为敏感（踩坑），用 "full body" 表述。
_VIEW_PREFIX = {
    0: (  # 正面全身（2026-08-07：与侧面/背面/特写同 img2img 管线，统一风格并刷新封面）
        "photorealistic photograph of one real person, the character stands "
        "upright with a natural relaxed stance, standing straight with both legs "
        "naturally extended and both feet flat on the ground, upright stance, "
        "front-facing, gaze directed slightly off-camera with a calm neutral expression, "
        "full body view from head to toe, complete feet and shoes visible, "
        "figure occupies the central part of the frame with clear margin on all sides, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "photorealistic human, natural skin texture"
    ),
    1: (  # 45° 侧身（3/4 侧视，短剧标准侧视）
        "photorealistic photograph of one real person, the character turns their body "
        "and head forty-five degrees to the left, three-quarter side view, "
        "face clearly angled showing a partial left side profile, "
        "both eyes visible, nose bridge and cheekbone clearly visible, "
        "natural side view without any distortion, "
        "full body view from head to toe, complete feet and shoes visible, "
        "figure occupies the central part of the frame with clear margin on all sides, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "photorealistic human, natural skin texture"
    ),
    2: (  # 背面
        "photorealistic photograph of one real person, viewed directly from behind, "
        "the character faces away from the camera with their back to the viewer, "
        "back of the head and back of the body visible, face completely hidden, "
        "full body view from head to toe, complete feet and shoes visible, "
        "figure occupies the central part of the frame with clear margin on all sides, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "photorealistic human, natural skin texture"
    ),
    3: (  # 正面脸部特写（胸像特写，核心锁脸参考）
        "photorealistic close-up portrait photograph of one real person, "
        "head and shoulders only, chest-up framing, no body below the chest, "
        "face filling the upper two-thirds of the frame, gaze directed slightly off-camera "
        "toward one side with a calm neutral expression, "
        "front-facing, the exact same face, hairstyle and outfit as the reference image, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "photorealistic human, natural skin texture, ultra sharp facial details, "
        "clear eyes, defined jawline, detailed hair strands, no distortion"
    ),
}

# 一致性约束：与参考图（封面）同角色，不改变外观
# 脸部/眼睛约束强化（用户反馈侧面眼睛扭曲变形）：五官自然对称、眼睛形状正确
CONSISTENCY = (
    "IMPORTANT: same character as the reference image, identical face, face shape, "
    "age, skin tone, hairstyle, outfit, accessories and body proportions, "
    "do not redesign or change the character's appearance, "
    "all views share the exact same light direction, soft even studio lighting. "
    "Keep the realistic photographic style exactly as the reference image, "
    "photorealistic human, not an illustration. "
    "Keep the face natural, symmetric and well-formed, no distortion, "
    "no facial deformation, no warped or stretched features, "
    "eyes natural, correctly shaped and aligned, no eye distortion, "
    "no deformed or weird eyes, "
    "natural profile features without any twisting or contortion"
)

# 扩写描述（expanded_description）尾部常带设定图标签（封面专用），如
# "Character design illustration, concept art style, ..., full-body character reference sheet"。
# 这些术语语义 = "正面展示的完整角色设定图"，在 img2img 四视图里会与视角/构图指令冲突，
# 压过 view_prefix 的"转身 45°/背面/胸像"约束（实测三视图全部输出正面全身，根因之一）。
# → 拼接四视图 prompt 前清洗掉，只保留角色外观描述本体。
_SHEET_TERMS_RE = re.compile(
    r"(character design illustration|concept art style|clean line art|"
    r"detailed anime-inspired rendering|anime-inspired rendering|"
    r"full-body character reference sheet|character reference sheet|"
    r"character sheet|reference sheet|character design|concept art|"
    r"character reference|white background|full-body illustration|"
    r"full body character)",
    re.IGNORECASE)


def _clean_base_prompt(p: str) -> str:
    """去掉扩写描述中的设定图/概念设计/动漫风标签，避免 img2img 视角指令被压制。

    同时清理删除短语后残留的孤立逗号（如 "...resolve. , , , , , ."）。
    """
    s = _SHEET_TERMS_RE.sub(" ", p)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"[,，][\s,，]*[.。]", ".", s)  # 逗号簇+句号 → 句号
    s = re.sub(r"[,，]{2,}", ",", s)  # 连续逗号合并
    return s.strip()


# 非站姿行为词（英文）——与 generate_asset_cover 封面链路同一套兜底（2026-08-18）：
# 角色 description 混入的剧情动作（如"蹲下与孩子平视"）会被扩写译成
# crouching/knees bent/eyes level with a child 写进扩写缓存，四视图任务直接
# 读取该扩写拼 prompt（img2img 参考图），封面清洗（generate_asset_cover）
# 管不到这条链路 → 四视图会画出蹲姿（半身特征格 + 三全身格全蹲）。
# 与封面同法：生图前剥离非站姿行为词，只保留常态人设外观。
_CROUCH_TERMS = re.compile(
    r"\b(crouch(?:ing|ed)?|squat(?:ting|ted)?|kneel(?:ing|ing down)?|"
    r"seated|sit(?:ting)?|sitting down|hunkered|hunched|stooped|"
    r"ducking|ducked|bent over|bending down)\b|"
    r"knees? bent|bent knees?|bent legs?|legs? bent|"
    r"eyes? level with (?:a|an|the|his|her|their)?\s?(?:small|young|little)?\s?child(?:ren)?|"
    r"face ?to ?face (?:with|at) (?:a|an|the|his|her|their)?\s?(?:small|young|little)?\s?child(?:ren)?",
    re.IGNORECASE,
)


def _strip_crouching_pose(text: str) -> str:
    """剥离角色描述中的蹲/坐/跪/弯腰等非站姿行为词，只保留常态人设。"""
    s = _CROUCH_TERMS.sub(" ", text)
    s = re.sub(
        r"\b(?:with|at|in|on|near|beside|from|toward|facing|opposite)\s+"
        r"(?:his|her|their|a|an|the|its)\s*,", ",", s,
    )
    s = re.sub(r",\s*,+", ",", s)  # 连续逗号合并
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",\s*\.", ".", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().strip(",").strip()


def _call_img2img_with_policy(provider, prompt, ref_url, opts):
    """img2img 调用 + 审核拦截重试（content_policy_violation 为间歇性误判，同 prompt 可自愈）。"""
    for attempt in range(_POLICY_MAX_RETRIES):
        try:
            return provider.imageToImage(prompt, [ref_url], opts)
        except Exception as e:
            if is_content_policy(e) and attempt < _POLICY_MAX_RETRIES - 1:
                logger.warning(
                    "四视图被安全策略拦截（第 %s/%s 次），重试中…", attempt + 1,
                    _POLICY_MAX_RETRIES,
                )
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise ValueError("四视图生成失败：无结果")


@celery_app.task(name="generate_asset_fourview", bind=True)
def generate_asset_fourview(self, task_id: str):
    db = SessionLocal()
    target_id = None
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        target_id = task.target_id
        asset = db.get(Asset, target_id)
        if asset is None:
            return
        if not asset.cover_url:
            raise ValueError("角色封面缺失，四视图需以封面为参考")
        model = db.get(Model, task.model_id)
        provider = ProviderRegistry.for_model(model)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        asset.status = MediaStatus.running
        db.commit()

        # 封面生成前自动扩写（与 generate_asset_cover 一致）：
        # 中文描述直喂英文模型会导致结果与描述不匹配
        # 2026-08-12：旧扩写缺人种声明（默认外国人）的角色需强制重扩，
        # 使新提示词的人种默认约束（中国/东亚人长相）生效。
        from app.services.asset_service import expand_description, needs_ethnicity_refresh
        if not asset.expanded_description or needs_ethnicity_refresh(asset):
            try:
                expand_description(db, asset.id)
                db.refresh(asset)
            except Exception as e:
                logger.warning("资产 %s 描述扩写失败，回退原描述: %s", asset.id, e)
                db.rollback()

        # 角色外观描述：优先用扩写，但需剥离扩写里自带的设定图标签词
        # （"character reference sheet/concept art"等会压制视角指令，见 _clean_base_prompt）
        base_prompt = _clean_base_prompt(asset.expanded_description or asset.description or asset.name)
        # 2026-08-12：剥离服装破损/磨损词（torn/ripped/frayed 等），
        # 旧扩写可能把"袖口磨白"放大为重度磨损，生图模型会渲染成服装撕裂。
        from app.services.asset_service import strip_cloth_damage
        base_prompt = strip_cloth_damage(base_prompt)
        # 2026-08-18：剥离非站姿行为词（防四视图下蹲）——封面清洗管不到本链路，
        # 扩写缓存仍可能含 crouching/蹲姿（"蹲下与孩子平视"误扩），四视图读取
        # 该扩写会画出蹲姿。与封面任务同法兜底，仅保留常态人设外观。
        base_prompt = _strip_crouching_pose(base_prompt)
        cap = getattr(model, "capability", None) or {}

        # 新链路：封面驱动 CharacterSheet LoRA 生成单张四格合一四视图
        if cap.get("ref_engine") == "flux2_9b_charsheet":
            _generate_charsheet(db, task_id, asset, provider, model, cap, base_prompt)
            return

        # ─── 旧链路（Z-Image 四张独立分图，兼容历史）────────────────────
        _generate_legacy(db, task_id, asset, provider, base_prompt)
    except TaskCancelledError:
        # 用户取消/项目删除：不回写 failed，保持 cancelled（资产已回退 pending）
        db.rollback()
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if target_id:
            asset = db.get(Asset, target_id)
            if asset:
                asset.status = MediaStatus.failed
                asset.error = msg
                db.commit()
    finally:
        db.close()


def _generate_charsheet(db, task_id, asset, provider, model, cap, base_prompt):
    """新链路：封面驱动 FLUX.2 Klein 9B + CharacterSheet LoRA → 单张四格合一四视图。

    产出 asset.character_sheet_url（1536×1024，R2V 参考图规格），
    生成后 flood-fill 统一底色为封面实测底色（消除四格拼接感）。
    """
    from app.models.project import Project
    from app.services.style_service import (
        get_effective_style_prompt,
        is_3d_cg_style,
        is_realistic_style,
    )

    project = db.get(Project, asset.project_id) if asset.project_id else None
    style_prompt = get_effective_style_prompt(db, project)
    # 2026-08-11：三态区分——写实 / 3D CG / 动漫插画，各自用专属措辞。
    # 旧逻辑只有"写实 / 非写实"二态，"3D半写实"被误归非写实 → 走
    # _CHARSHEET_PREFIX_ANIME "Clean anime style" → 四视图变动漫风。
    if is_3d_cg_style(db, project):
        prefix = _CHARSHEET_PREFIX_3D
    elif is_realistic_style(db, project):
        prefix = _CHARSHEET_PREFIX
    else:
        prefix = _CHARSHEET_PREFIX_ANIME
    parts = [prefix]
    if style_prompt:
        parts.append(f"Art style: {style_prompt}")
    parts.append(base_prompt)
    # 2026-08-12：质量修饰词 + 容貌美学段（社区最佳实践，与封面链路一致）。
    # 四视图/立绘只加质量词与容貌词，不放构图/视角约束（视角由 prefix 控制）。
    # 容貌词必须带正常比例约束，防止面部聚焦词引导模型放大头部（"巨头"问题）。
    parts.append(
        "A highly detailed image with ultra high definition, sharp focus and fine intricate "
        "detail, accurate color and tonal gradation, refined craftsmanship quality, "
        "handsome attractive appearance, refined facial features, "
        "natural real skin texture with visible pores and realistic skin fine detail, "
        "natural confident expression, "
        "normal head-to-body ratio, head size proportionate to body, "
        "do not enlarge the head or face, natural realistic body proportions"
    )
    prompt = "\n\n".join(parts)
    opts = ImageOpts(
        width=int(cap.get("width", 1536)),
        height=int(cap.get("height", 1024)),
    )
    handle = _call_img2img_with_policy(provider, prompt, asset.cover_url, opts)
    # ComfyUI 异步工作流需轮询 history 直到 completed；约 4 分钟
    result = run_with_polling(db, task_id, provider, handle, poll_interval=5, timeout=900)
    if not result.imageUrls:
        raise ValueError("四视图生成失败：无返回图")
    local_url = download_to_local(
        result.imageUrls[0], subdir=f"assets/{asset.id}",
        filename="character_sheet.png", task_id=task_id,
    )
    # 2026-08-11：四视图与封面同法——原样使用 ComfyUI 服务器原图，不做任何
    # 像素级后处理（不 flood-fill 染背景、不压高光、不注入 ICC）。此前
    # _unify_sheet_background 染背景会提亮背景/污染人物浅色区域，导致曝光问题。
    # 背景统一交给 prompt 的 light grey 约束，与封面修复（2026-08-10）一致。
    final_url = local_url
    asset.character_sheet_url = final_url
    asset.status = MediaStatus.succeeded
    update_task(
        db, task_id, status=TaskStatus.succeeded, progress=100,
        result_url=final_url, finished_at=now(),
    )
    db.commit()
    logger.info("四视图（CharacterSheet 四格合一）已生成: %s", final_url)


def _generate_legacy(db, task_id, asset, provider, base_prompt):
    """旧链路：Z-Image img2img 四张独立分图（正面=封面覆盖 + 侧面/背面/特写）。"""
    from app.models.project import Project
    from app.services.style_service import (
        get_effective_style_prompt,
        is_3d_cg_style,
        is_realistic_style,
    )

    project = db.get(Project, asset.project_id) if asset.project_id else None
    style_prompt = get_effective_style_prompt(db, project)
    negative_prompt = (
        "worst quality, low quality, lowres, blurry, bad anatomy, bad hands, "
        "missing fingers, extra digits, deformed hands, distorted face, "
        "mutated face, deformed face, asymmetrical face, malformed facial "
        "features, cross-eyed, weird eyes, distorted eyes, deformed eyes, "
        "bad eyes, uneven eyes, asymmetric eyes, broken features, ugly face, "
        "extra fingers, fused fingers, six fingers, twisted hands, "
        # 2026-08-12：巨头/比例失调负面词（防头部放大）
        "big head, large head, oversized head, enlarged head, huge head, giant head, "
        "head too large, disproportionate head, head to body ratio wrong, "
        "out of proportion, distorted proportions, "
        # 2026-08-18：非站立姿态负面词（防下蹲/坐/跪/躺）——角色必须正面全身站立
        "crouching, crouched, squatting, squatting down, squat, crouching down, "
        "sitting, seated, sitting down, kneeling, kneeling down, on knees, "
        "lying down, lying on the ground, lying on ground, reclining, laying down, "
        "hunched over, hunched, stooped, stooping, bent over, bent forward, "
        "bent knees, bent legs, legs bent, folded legs, cross-legged, "
        "half kneeling, hunkered down, ducking, ducked, crouched legs, "
        # 2026-08-12：服装破损负面词（防撕裂/破洞/磨损断裂）
        "torn clothes, torn clothing, ripped clothes, ripped clothing, tattered, "
        "torn fabric, ripped fabric, holes in clothes, torn sleeves, ripped jacket, "
        "torn pants, damaged clothing, frayed edges, ripped seams, "
        "watermark, text, signature, duplicate, extra person, "
        "grey background, gray background, beige background, dark background, "
        "shadow on background, "
        "background, scenery, environment, landscape, nature, city, room, "
        "indoor, outdoor, street, forest, sky, mountain, building, setting, scene, "
        "cropped, cut off, feet cut off, legs cut off, out of frame, zoomed in"
    )
    # 2026-08-11：三态负面词——写实排除动漫/3D/插画；3D CG 类排除动漫/插画
    # 但保留 "3d render"（目标风格本身）；动漫等非写实不追加排除词。
    if is_3d_cg_style(db, project):
        negative_prompt = (
            f"{negative_prompt}, anime, anime style, cartoon, cartoon style, "
            "manga, comic, illustration, 2d art, digital art, painting, drawing, "
            "flat coloring, anime eyes, anime face, lineart, cel shading, "
            "watercolor, oil painting, sketch"
        )
    elif is_realistic_style(db, project):
        negative_prompt = (
            f"{negative_prompt}, anime, anime style, cartoon, cartoon style, "
            "manga, comic, illustration, 2d art, digital art, painting, drawing, "
            "3d render, cg render, stylized, non-realistic, flat coloring, "
            "anime eyes, anime face, lineart, cel shading"
        )
    # 四视图全部走同一条 img2img 管线（denoise 0.9 + 清洗 prompt + CONSISTENCY），
    # 保证四张风格统一。正面（index 0）生成结果覆盖 cover_url——用户 2026-08-07
    # 反馈三张新图比旧封面/正面效果好，要求按同风格刷新封面与正面图。
    # 顺序：先生成正面 → 更新封面 → 侧面/背面/特写以新封面为参考（风格链统一）。
    urls: list[str] = []
    for i, view_prefix in _VIEW_PREFIX.items():
        parts = [view_prefix]
        if style_prompt:
            parts.append(f"Art style: {style_prompt}")
        parts.append(base_prompt)
        parts.append(CONSISTENCY)
        # 2026-08-12：质量修饰词 + 容貌美学段（社区最佳实践），放 CONSISTENCY 之后。
        # 容貌词带正常比例约束，防止头部放大（"巨头"问题）。
        parts.append(
            "A highly detailed image with ultra high definition, sharp focus and fine intricate "
            "detail, accurate color and tonal gradation, refined craftsmanship quality, "
            "handsome attractive appearance, refined facial features, "
            "natural real skin texture with visible pores and realistic skin fine detail, "
            "natural confident expression, "
            "normal head-to-body ratio, head size proportionate to body, "
            "do not enlarge the head or face, natural realistic body proportions"
        )
        prompt = "\n\n".join(parts)
        view_opts = ImageOpts(
            ratio="1:1", size="2K", negative_prompt=negative_prompt,
            base=1024,  # 1024×1024 原生最优档（Z-Image 约 1M 像素）
            denoise=0.9,
        )
        ref_url = asset.cover_url
        handle = _call_img2img_with_policy(provider, prompt, ref_url, view_opts)
        result = run_with_polling(db, task_id, provider, handle, poll_interval=3, timeout=300)
        if not result.imageUrls:
            raise ValueError(f"四视图 view_{i} 生成失败：无返回图")
        filename = "cover.png" if i == 0 else f"view_{i}.png"
        final_url = download_to_local(
            result.imageUrls[0], subdir=f"assets/{asset.id}",
            filename=filename, task_id=task_id,
        )
        if i == 0:
            asset.cover_url = final_url
            db.commit()
        urls.append(final_url)
        update_task(db, task_id, progress=25 + i * 25, last_heartbeat_at=now())
        db.commit()
        logger.info("四视图 view_%s 已生成: %s", i, final_url)

    if len(urls) != 4:
        raise ValueError("四视图分图数量异常")
    asset.four_view_urls = urls
    asset.status = MediaStatus.succeeded
    update_task(
        db, task_id, status=TaskStatus.succeeded, progress=100,
        result_url=urls[0], finished_at=now(),
    )
    db.commit()
