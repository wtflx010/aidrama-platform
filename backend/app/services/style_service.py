"""风格解析服务：按优先级解析项目的有效风格 prompt 片段。

优先级：
1. project.style_id 非空 → 查 ArtStyle.prompt_fragment
2. project.art_style_prompt 非空 → 直接用
3. 都为空 → 系统兜底：默认写实真人风格（防止角色图混出动漫/3D/插画等"非真人"效果）
"""
from sqlalchemy.orm import Session

from app.models.art_style import ArtStyle
from app.models.project import Project

# 系统兜底风格片段（与内置"写实摄影"预设一致）：
# 项目未配置任何风格时注入，保证角色/场景/道具资产生成默认真实感，
# 避免模型自由发挥输出动漫/3D/插画等非真人效果。
DEFAULT_REALISTIC_STYLE = (
    "hyper-realistic professional photography, shot on 85mm f/1.4 lens, "
    "natural soft window lighting, true-to-life skin texture and pores, "
    "crisp tack-sharp focus, accurate real-world colors, editorial portrait photography, "
    "national geographic style, 8k uhd detail, candid realistic moment"
)


def get_effective_style_prompt(db: Session, project: Project | None) -> str:
    """解析项目有效风格 prompt 片段（英文，注入生图 prompt）。

    项目未配置任何风格时返回 DEFAULT_REALISTIC_STYLE（系统兜底写实），
    不再返回 None——保证所有资产生成都有明确风格约束。
    """
    if not project:
        return DEFAULT_REALISTIC_STYLE
    # 1) 预设风格外键
    if project.style_id:
        style = db.get(ArtStyle, project.style_id)
        if style and style.prompt_fragment:
            return style.prompt_fragment
    # 2) 自定义风格文本
    if project.art_style_prompt:
        return project.art_style_prompt.strip() or DEFAULT_REALISTIC_STYLE
    # 3) 都无 → 系统兜底默认写实
    return DEFAULT_REALISTIC_STYLE


def has_explicit_style(project: Project | None) -> bool:
    """项目是否显式配置了风格（style_id 或 art_style_prompt 任一非空）。"""
    return bool(project and (project.style_id or (project.art_style_prompt or "").strip()))


_STYLE_ANCHOR_PROMPT = (
    "你是短剧美术风格总监。根据下面的短剧故事梗概与题材，提炼该剧统一的美术风格锚点"
    "（英文，60~100 词，只输出风格片段本身、不要解释）："
    "必须覆盖 4 个维度——"
    "1) 时代与世界观：现代都市/近现代/古代仙侠/架空古风等，明确取材文化（如中国当代）与"
    "生活质感（老宅、城中村、写字楼等）；"
    "2) 整体美术调性：写实摄影感/电影感/生活流纪实/东方幻想等，色调与氛围（如暖旧色调、"
    "光影对比强烈、烟火气）；"
    "3) 视觉禁项：明确禁止与背景冲突的风格（如现代剧禁欧式古典/维多利亚/哥特；古装剧禁"
    "现代都市元素）；"
    "4) 一致性要求：全剧角色服装、场景建筑、道具材质统一遵循该时代特征，角色为中国人/东亚"
    "人长相时默认东亚人种特征。"
)
_STYLE_ANCHOR_TAIL = " Art style anchor for a Chinese web drama; photorealistic film look."


def ensure_project_art_style(db: Session, project: Project | None) -> str:
    """项目无显式风格时，用 LLM 依据故事梗概生成统一美术风格锚点并落库。

    2026-08-19 新增：解决「生图风格割裂」——此前项目 style_id=None 且
    art_style_prompt 为空时走系统兜底写实措辞（无故事时代/文化锚定），导致
    现代都市剧的主角和场景被自由发挥成道士/欧式。此函数在项目创建后自动生成
    「故事绑定」的风格片段，保证项目下所有资产生图风格统一。

    返回最终生效的风格片段（生成失败等异常被吞掉，回退原逻辑）。
    """
    if not project:
        return DEFAULT_REALISTIC_STYLE
    if has_explicit_style(project):
        return get_effective_style_prompt(db, project)
    try:
        from app.providers.registry import ProviderRegistry
        from app.models.model_config import ModelType
        from app.services.keyframe_service import _resolve_model

        synopsis = (project.synopsis or project.title or "").strip()
        if not synopsis:
            return DEFAULT_REALISTIC_STYLE
        model = _resolve_model(db, None, ModelType.text, "expand")
        provider = ProviderRegistry.for_model(model)
        resp = provider.chat(
            [
                {"role": "system", "content": "只输出英文风格片段，不要任何解释或多余文字。"},
                {"role": "user", "content": f"故事梗概：{synopsis[:600]}\n\n{_STYLE_ANCHOR_PROMPT}"},
            ],
            max_tokens=400,
        )
        content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            return DEFAULT_REALISTIC_STYLE
        # 拼上统一的短剧摄影落点，避免模型只给「氛围词」缺摄影质感兜底
        fragment = f"{_STYLE_ANCHOR_TAIL} {content}"
        project.art_style_prompt = fragment
        db.commit()
        return fragment
    except Exception:  # noqa: BLE001
        db.rollback()
        return DEFAULT_REALISTIC_STYLE


# 真人写实类内置风格名（资产生成须追加"非真人排除"负面词）：
# 写实摄影/电影感/黑白/仿真人效果为真人写实类；其余（动漫/水墨/水彩/油画/3D渲染/像素/赛博朋克/AI漫剧）
# 本身是非写实风格，追加排除词会与所选风格冲突，故不追加。
# 2026-08-10：新增「仿真人效果」（真人实拍级写实，面向短剧）加入写实类。
_REALISTIC_STYLE_NAMES = {"写实摄影", "电影感", "黑白", "仿真人效果"}


def is_realistic_style(db: Session, project: Project | None) -> bool:
    """当前项目有效风格是否为真人写实类 → 资产生成应追加"非真人排除"负面词。

    判定顺序：
    1. 项目未显式配置风格 → 系统兜底写实 → True
    2. style_id 指向内置写实类风格（写实摄影/电影感/黑白）→ True；其余内置风格 → False
    3. 自定义 art_style_prompt → 含动漫/插画等非真人关键词视为非写实，否则按写实兜底
    """
    if not project:
        return True
    if project.style_id:
        style = db.get(ArtStyle, project.style_id)
        if style and style.name in _REALISTIC_STYLE_NAMES:
            return True
        return False
    text = (project.art_style_prompt or "").strip()
    if not text:
        return True
    low = text.lower()
    non_realistic_keys = (
        "anime", "cartoon", "animation", "illustration", "painting", "drawing",
        "watercolor", "ink", "pixel art", "3d render", "cel shading", "manga",
    )
    return not any(k in low for k in non_realistic_keys)


# 3D CG 渲染类内置风格名（3D半写实/3D渲染）：
# 介于写实与动漫之间——既不能用 "photorealistic photograph" 措辞（压成真人照片），
# 也不能用 "anime character" 措辞（压成动漫）；须用 3D CG render 专属措辞，
# 负面词排除动漫/插画但保留 3d render（2026-08-11 修复：3D半写实曾误归动漫类）。
_3D_CG_STYLE_NAMES = {"3D半写实", "3D渲染"}
# 自定义 art_style_prompt 中的 3D CG 关键词（命中即按 3D 类处理）
_3D_CG_KEYS = (
    "3d render", "3d cg", "cg render", "3d model", "3d character",
    "unreal engine", "octane render", "3d semi-realistic", "semi-realistic 3d",
    "high-end 3d", "3d half-realistic", "half-realistic 3d",
)


def is_3d_cg_style(db: Session, project: Project | None) -> bool:
    """当前项目有效风格是否为 3D CG 渲染类（3D半写实/3D渲染）。

    判定顺序：
    1. 项目无风格 / 无项目 → False（走写实兜底或其他分支）
    2. style_id 指向内置 3D 类风格（3D半写实/3D渲染）→ True
    3. 自定义 art_style_prompt 含 3D 渲染关键词 → True
    """
    if not project:
        return False
    if project.style_id:
        style = db.get(ArtStyle, project.style_id)
        if style and style.name in _3D_CG_STYLE_NAMES:
            return True
        return False
    text = (project.art_style_prompt or "").strip()
    if not text:
        return False
    low = text.lower()
    return any(k in low for k in _3D_CG_KEYS)


# ─── MiniMax H3 风格适配参数 ────────────────────────────────────
# 按风格差异化调 SigmaShift 双流 shift 与采样步数、负面词补充。
# 依据：SigmaShift 的 shift 控制噪声调度在 sigma 区的分布——高 shift 把更多
# 采样步放在高 sigma（全局结构/光影氛围），低 shift 偏向低 sigma（细节锐利/线条）。
# 写实/电影/暗调风格需要厚重光影 → 高 shift；动漫/像素/水墨线条清晰 → 中低 shift。
#
# 2026-08-27（慢动作修复 方案B）：系统生产链路统一走 8 步 Turbo 蒸馏
# （capability.steps=8）→ 高 shift（11~13）在 8 步下把低噪声区（时间/运动细节
# + 面部纹理）压缩到只剩 1~2 步，动作被摊平 →「慢动作」观感，中远景人脸细节
# 也吃亏。A/B 实证（同 seed/同 prompt/FL2V v1.1 bf16，H3资源库 09 附录）：
# shift_video=9 vs 12 平均运动能量持平、峰值运动更高、全帧/面部锐度多数点更优，
# 音频不受影响。故全档 shift_video 回调至 9.0~9.5。
#
# 2026-08-27（同日二次）：音频收敛是【模型+步数】属性、与艺术风格无关——
#  原本风格档自带的 shift_audio(3.0~3.5) 会遮蔽 capability 的音频铁律
#  （FL2V=6.0 / R2V=3.0，8 步 Turbo 下已验证收敛），已从风格档剥离；
#  shift_audio 一律由模型 capability 兜底（comfyui.py opts 为 None 时回退），
#  风格档只保留 shift_video / steps / negative_extra（steps 仍为历史手感值，
#  生产 8 步提速后不生效，仅作记录）。
_STYLE_VIDEO_PARAMS: dict[str, dict] = {
    "电影感": {
        "shift_video": 9.5, "steps": 32,
        "negative_extra": "anime, cartoon, illustration, flat lighting, video game cg, amateur footage",
    },
    "写实摄影": {
        "shift_video": 9.5, "steps": 30,
        "negative_extra": "anime, cartoon, illustration, 3d render, oil painting, oversaturated",
    },
    "动漫": {
        "shift_video": 9.0, "steps": 28,
        "negative_extra": "photorealistic, realistic photo, 3d render, oil painting, watercolor, live action",
    },
    # 2026-08-09 新增「AI 漫剧」风格（semi-realistic anime 二次元写实）：
    # 介于写实与动漫之间，参数取两者中间（shift 保留光影、steps 30 细节适中），
    # 负面词排除纯真人照片感与纯 3D/手绘，保持 2.5D 半写实质感。
    # 2026-08-27：shift_video 12.0 → 9.0（8 步 Turbo 下留足低噪声细节/运动区间）
    "AI 漫剧": {
        "shift_video": 9.0, "steps": 30,
        "negative_extra": "photorealistic photography, realistic photo, pure 3d render, oil painting, watercolor, pixel art, flat 2d cel shading, thick heavy lineart",
    },
    # 2026-08-10 新增「仿真人效果」风格（真人实拍级写实，面向短剧）：
    # 最接近真人实拍——高 shift 保厚重光影、steps 32 细节充分，
    # 负面词排除动漫/卡通/3D/插画等一切非真人元素，维持纪录片级真实感。
    # 2026-08-10（调研后补强）：补充 AI 脸典型败笔——磨皮过度/塑料皮肤/眼神空洞/
    # 人物与背景脱节（色温/光影不一致），并排除纯静态摆拍感。
    "仿真人效果": {
        "shift_video": 9.5, "steps": 32,
        "negative_extra": "anime, cartoon, cel shading, 2d animation, illustration, 3d render, cgi render, stylized, oil painting, watercolor, oversaturated, plastic skin, waxy face, oversmoothed skin, airbrushed, doll-like, glassy empty eyes, blank stare, person pasted onto background, mismatched lighting, flat stiff pose",
    },
    "3D半写实": {
        "shift_video": 9.0, "steps": 30,
        "negative_extra": "anime, cartoon, cel shading, 2d animation, illustration, watercolor, oil painting, photorealistic photo, flat 2d cel shading, thick heavy lineart",
    },
    "赛博朋克": {
        "shift_video": 9.5, "steps": 32,
        "negative_extra": "anime, watercolor, pastel, bright daylight, vintage, retro 90s",
    },
    "水彩": {
        "shift_video": 9.0, "steps": 26,
        "negative_extra": "photorealistic, oil painting, thick impasto, 3d render, pixel art",
    },
    "油画": {
        "shift_video": 9.5, "steps": 30,
        "negative_extra": "photorealistic, watercolor, anime, pixel art, flat vector",
    },
    "3D渲染": {
        "shift_video": 9.0, "steps": 28,
        "negative_extra": "photorealistic photo, anime, 2d illustration, watercolor, claymation",
    },
    "像素艺术": {
        "shift_video": 9.5, "steps": 24,
        "negative_extra": "photorealistic, smooth gradients, high detail texture, 3d render, watercolor",
    },
    "黑白": {
        "shift_video": 9.5, "steps": 30,
        "negative_extra": "vibrant colors, oversaturated, colorful, rainbow",
    },
}

# 自定义风格 / 未知风格回退：写实电影基线（官方模板默认 12.0/3.0）
# 2026-08-27：默认回退 12.0 → 9.0（与 capability 对齐，8 步 Turbo 下实测更优）
_DEFAULT_VIDEO_PARAMS: dict = {
    "shift_video": 9.0, "steps": 30, "negative_extra": "",
}


def get_style_video_params(db: Session, project: Project | None) -> dict:
    """按项目风格解析 MiniMax H3 视频生成适配参数（shift_video / negative_extra）。

    shift_audio 已从风格档剥离（2026-08-27）：音频收敛是模型+步数属性，
    一律由模型 capability 铁律兜底（FL2V=6.0 / R2V=3.0），不在本函数返回。
    2026-09-16：风格档的 steps（24~32）为历史手感值、生产 8 步下不生效，仅记录，
    故在返回前移除，避免被下游/前端误当作有效参数。
    """
    if not project or not project.style_id:
        out = dict(_DEFAULT_VIDEO_PARAMS)
    else:
        style = db.get(ArtStyle, project.style_id)
        name = style.name if style else ""
        out = dict(_STYLE_VIDEO_PARAMS.get(name, _DEFAULT_VIDEO_PARAMS))
    out.pop("steps", None)
    return out
