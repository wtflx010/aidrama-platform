"""资产封面/场景图/道具图生成任务：textToImage → 下载落库到 cover_url。

用于角色封面、场景图、道具图（统一走 textToImage，区别仅在 scene_code 选模型）。
P4：注入项目风格 prompt 片段 + 按项目 aspect_ratio 生成，保证资产与成片风格比例一致。
"""
import logging
import re
import time

from app.database import SessionLocal
from app.models.asset import Asset, AssetType
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
# 模块级导入：_clean_style_for_isolated 在模块级函数中引用，不能依赖其他函数体内的局部 import
from app.services.style_service import DEFAULT_REALISTIC_STYLE

logger = logging.getLogger(__name__)

# 审核拦截重试：最多 3 次（间隔 3s→6s），与关键帧链路一致。
# Agnes 审核拦截以 HTTP 400 返回，is_content_policy 会检查响应体，
# 同 prompt 重试可自愈（间歇性误判，实测偶发拦截）。
_POLICY_MAX_RETRIES = 3

# 按 AssetType 区分生图前缀（放 prompt 开头，权重最大）：
# - 角色：白底全身设定图，无背景，便于关键帧 img2img 做一致性参考。
#   封面必须是"完整全身含脚"（四视图正面=封面本身，且特写=封面裁头部，
#   头部需固定在上部中央区域才能稳定裁剪）→ 加 head/feet 构图约束。
# - 道具：白底单品图，无背景
# - 场景：保留环境背景，不加白底（场景图本就需要氛围）。
#   场景封面必须"无人"：作为关键帧主参考时，参考图里的人物会被模型保留/污染
#   关键帧人物（img2img 参考图权重极高），加 no people 约束从源头消除。
# 注意：Agnes 审核对 "standing pose" 误判为敏感（见四视图踩坑），改用 "full body character"。
# 2026-08-09 修复：非写实风格（动漫等）下 "photorealistic photograph + pure white"
# 与风格矛盾 → 模型生成浅蓝等怪异底色且与人物混色。写实走照片措辞，动漫等
# 非写实走 "anime illustration + pure white" 措辞（风格用词变，白底不变）。
# 2026-08-09 规则（适用于所有风格）：封面图底色统一纯白，角色为正面全身照。
_TYPE_PREFIX: dict[AssetType, str] = {
    AssetType.character: (
        "full body photograph from head to toe, complete feet and shoes visible, "
        "front-facing, gaze directed slightly off-camera with a calm neutral expression, "
        # 2026-08-18：站姿正面词（封面必须是正面全身站立照，防下蹲/蜷缩变形）。
        # 注意避用 "standing pose"（Agnes 审核误判敏感，见四视图踩坑），
        # 用 "standing upright/stands straight" 等效安全措辞。
        "standing upright, standing straight, legs naturally straight, "
        "both feet flat on the ground, upright stance, "
        "head positioned in the upper central area of the frame, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "no background, no scenery, no environment, "
        "isolated on light grey, centered composition"
    ),
    AssetType.prop: (
        "single prop product shot, isolated object, floating in air, "
        "no platform, no pedestal, no stand, no table, no shelf, no ground, "
        "no shadow, no reflection, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "no background, no scenery, no environment, "
        "isolated on light grey, centered composition"
    ),
    AssetType.scene: (
        "empty environment scene, no people, no characters, no human figures, "
        "no person, no crowd, no portraits, environment and setting only"
    ),
}
# 写实摄影类风格（写实摄影/电影感/黑白/仿真人效果/未选风格兜底写实）场景封面前缀：
# 2026-08-18 修复：此前所有风格共用通用 "empty environment scene" 前缀，无摄影
# 措辞——写实风格下场景图真实感全靠中段一行 Art style 撑着，权重被前后指令
# 稀释 → 写实场景改用摄影措辞放 prompt 开头（权重最大），把 "photorealistic
# photograph" 作为主载体，与角色图三态前缀对称。
_TYPE_PREFIX_SCENE_PHOTO = (
    "photorealistic photograph of a real-world empty environment, "
    "no people, no characters, no human figures, no person, no crowd, no portraits, "
    "environment and setting only, realistic natural materials and textures, "
    "real camera photograph, natural photorealistic lighting"
)
# 非写实风格（动漫/插画等）角色/道具封面前缀：浅灰底 + 动漫措辞
_TYPE_PREFIX_NON_REALISTIC: dict[AssetType, str] = {
    AssetType.character: (
        "full body anime character from head to toe, complete feet and shoes visible, "
        "front-facing, gaze directed slightly off-camera with a calm neutral expression, "
        "standing upright, standing straight, legs naturally straight, "
        "both feet flat on the ground, upright stance, "
        "head positioned in the upper central area of the frame, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "no background, no scenery, no environment, "
        "isolated on light grey, centered composition"
    ),
    AssetType.prop: (
        "single prop illustration, isolated object, floating in air, "
        "no platform, no pedestal, no stand, no table, no shelf, no ground, "
        "no shadow, no reflection, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "no background, no scenery, no environment, "
        "isolated on light grey, centered composition"
    ),
}
# 3D CG 渲染类（3D半写实/3D渲染）角色/道具封面前缀：浅灰底 + 3D CG 措辞。
# 2026-08-11 修复：此前 3D 类风格被 is_realistic_style 误判为非写实，走了
# _TYPE_PREFIX_NON_REALISTIC 的 "anime character" 措辞 → 封面生成成动漫风。
# 3D 类必须用 "3D character render" 措辞，风格片段（Art style: ...）才有载体。
_TYPE_PREFIX_3D: dict[AssetType, str] = {
    AssetType.character: (
        "full body 3D CG character render from head to toe, complete feet and shoes visible, "
        "front-facing, gaze directed slightly off-camera with a calm neutral expression, "
        "standing upright, standing straight, legs naturally straight, "
        "both feet flat on the ground, upright stance, "
        "head positioned in the upper central area of the frame, "
        "sculpted 3D model, realistic proportions, 3D facial features, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "no background, no scenery, no environment, "
        "isolated on light grey, centered composition"
    ),
    AssetType.prop: (
        "single 3D prop render, isolated 3D object, floating in air, "
        "no platform, no pedestal, no stand, no table, no shelf, no ground, "
        "no shadow, no reflection, "
        "light grey background, solid light grey backdrop, clean light grey #EBEBEB background, "
        "no background, no scenery, no environment, "
        "isolated on light grey, centered composition"
    ),
}

# negative_prompt：角色/道具排除背景相关词（Agnes 实测支持 negative_prompt）
# P4：补质量类负面词（畸形/模糊/多余人物），叠加背景排除词
# 场景图不需要排除背景，但必须排除人物（防止场景封面带人污染关键帧）
_NEGATIVE_BG = (
    "worst quality, low quality, lowres, blurry, bad anatomy, bad hands, "
    "missing fingers, extra digits, deformed hands, distorted face, "
    "watermark, text, signature, duplicate, extra person, "
    # 2026-08-12：补充容貌丑化类负面词（社区最佳实践）
    "ugly face, deformed face, asymmetrical face, malformed facial features, "
    "cross-eyed, weird eyes, distorted eyes, ugly, plain, unattractive, "
    "uncanny, doll-like, plastic skin, waxy skin, stiff expression, frozen face, "
    # 2026-08-12：巨头/比例失调负面词（防头部放大）+ 2026-08-18 强化的 Q版/卡通风
    "big head, large head, oversized head, enlarged head, huge head, giant head, "
    "head too large, disproportionate head, head to body ratio wrong, "
    "out of proportion, distorted proportions, "
    "chibi, chibi style, q-version, q version, q-format, cute cartoon proportions, "
    "cartoonish proportions, toddler proportions, doll proportions, "
    "big headed cartoon, Funko style, bobblehead, 2 to 3 head tall, 3 head body, "
    "kawaii chibi, oversized cranium, huge cranium, abnormally large skull, "
    "cute chibi child, deformed body, tiny adult body, shrunken body, "
    "disproportionately small body, miniature adult, small shrunken figure, "
    # 2026-08-18：非站立姿态负面词（防"下蹲"封面）——角色封面必须是
    # 正面全身站立照，蹲/坐/跪/躺/弯腰/蜷缩一律排除。提示词与负面词双保险。
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
    "dark background, colored background, textured background, "
    "shadow on background, "
    "cropped, cut off, feet cut off, legs cut off, head cut off, out of frame, zoomed in, "
    "half body, half body shot, upper body, waist-up, bust shot, close-up, portrait only, "
    "torso, chest-up, shoulders-up, "
    "background, scenery, environment, landscape, nature, city, room, "
    "indoor, outdoor, street, forest, sky, mountain, building, setting, scene"
)
_NEGATIVE_SCENE = (
    "worst quality, low quality, lowres, blurry, watermark, text, signature, "
    "person, people, human, character, figure, crowd, face, portrait, extra person"
)
# 角色/道具封面注入风格片段时剥离环境/场景词（2026-08-10 修复）：
# 项目自定义 art_style_prompt 可能含"废墟古战场、岩石残垣、风沙流动"等环境描述，
# 原样注入封面 prompt 会引导模型画出背景 → 封面非白底，flood-fill 也染不干净。
# 角色/道具是"白底孤立主体"设定图，风格只保留质感/光影/色调词，剔除一切环境词。
_ENV_TERMS = [
    "环境氛围", "环境", "背景", "场景", "废墟", "古战场", "战场", "岩石", "残垣",
    "风沙", "沙漠", "天空", "山川", "大地", "建筑", "宫殿", "森林", "城市",
    "街道", "室内", "室外", "水域", "湖泊", "草木", "植被", "树木", "云层",
    "荒芜", "体积雾", "雾气", "浓雾", "地点", "场地", "地平线", "阴天", "夕阳",
    "黄昏", "尘土", "硝烟",
]


def _clean_style_for_isolated(style: str) -> str:
    """清洗风格片段：删除环境/场景词，仅保留质感与光影类描述（角色/道具封面用）。"""
    s = style
    for term in _ENV_TERMS:
        s = s.replace(term, "")
    s = s.replace("，", ",").replace("、", ",")
    s = re.sub(r",\s*,+", ",", s)
    s = s.strip(", ")
    return s or DEFAULT_REALISTIC_STYLE


# 角色描述中的技能/变身/神通状态词（英文）——2026-08-11 修复：
# 扩写描述若混入技能状态（如「法天象地化为百丈巨人虚影」的 ethereal/semi-transparent/
# phantom/silhouette 等），模型会把虚影/半透明/无头人形画进封面，产出"正背面服装
# 展示图"+ 幽灵化人物。封面是常态设定图，须剥离一切技能状态特征，只保留常态人设。
_SKILL_STATE_TERMS = re.compile(
    r"\b(semi-transparent|translucent|phantom|ethereal|ghost(?:ly)?|apparition|"
    r"manifestation|spirit form|astral form|colossal|giant|hundred-zhang|"
    r"hundred-zhang-high|towering|silhouette|imposing silhouette|majestic silhouette|"
    r"scaled human proportions|inner luminescence|luminescent|glowing|radiant aura|"
    r"afterimage|projection|avatar form|divine form|celestial form)\b",
    re.IGNORECASE,
)
# 中文技能状态词（与英文词表对应；剥离前先统一替换为英文等价词，再走英文剥离）
_SKILL_STATE_CN = {
    "半透明": " ", "虚影": " ", "幻影": " ", "幽灵": " ", "法相": " ",
    "百丈": " ", "巨人": " ", "发光": " ", "光晕": " ", "剪影": " ",
    "透光": " ", "灵光": " ", "残影": " ", "投影": " ",
}


def _strip_skill_state(text: str) -> str:
    """剥离角色描述中的技能/变身/神通状态词（含中英文），只保留常态人设。

    仅用于角色封面/四视图的常态设定图；关键帧/视频的分镜级 prompt 不走此清洗，
    技能状态由分镜描述本身承载（画面正需要虚影/巨人等表现）。
    """
    s = text
    for cn, repl in _SKILL_STATE_CN.items():
        s = s.replace(cn, repl)
    s = _SKILL_STATE_TERMS.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r",\s*\.", ".", s)
    return s.strip().strip(",").strip()


# 中文字符（CJK 统一表意文字区）——2026-08-10 修复：
# Z-Image base（Qwen-Image 架构）原生支持中文文字渲染，prompt 里残留的任何中文字
# （包括 LLM 扩写描述里的中文专名如「玄色」「玄光」，以及此前误入 prompt 的中文维护
# 注释）都会被模型当作画面文字渲染进图片（实测道具图出现 "2026-08-10 用户反馈…" 文字）。
# 资产封面是白底设定图，画面不允许出现任何文字 → 生图前剥离 prompt 中的全部中文字。
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _strip_cjk(text: str) -> str:
    """剥离 prompt 中的所有中文字符（含标点），并清理剥离后的残留逗号。"""
    s = _CJK_RE.sub(" ", text)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r",\s*,+", ",", s)  # 连续逗号合并
    s = re.sub(r",\s*\.", ".", s)
    return s.strip().strip(",").strip()


# 非站姿行为词（英文）：2026-08-18 修复——角色 description 里可能混入剧情动作
#（如"蹲下与孩子平视耐心教导"），旧缓存的扩写描述会把 crouching/knees bent/
# eyes level with a child 等姿态行为译成英语写进生图 prompt 主体。prompt 主体
# 里的正面 crouching 权重远高于前缀/负面词的站姿约束（负面词只能"防止"漂移，
# 压不住主体明确描写的动作），封面必然生成下蹲图。
# 封面是"正面全身站立常态设定图"——生图前剥离一切非站姿行为姿态，只保留常态
# 人设外观（发型/五官/服装/体态/气质）。新扩写已由 expand_description 提示词
# 的"7) 姿态：必须站立"约束，此清洗兜底已有缓存与手动改写的 description。
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
    """剥离角色描述中的蹲/坐/跪/弯腰等非站姿行为词，只保留常态人设。

    仅用于角色封面/四视图的常态设定图；关键帧/视频的分镜级 prompt 不走此
    清洗——分镜描述里的蹲/坐本就是画面需要的动作。
    """
    s = _CROUCH_TERMS.sub(" ", text)
    # 清理删除点留下的句法残渣（"with his ," "at the ," 等介词+形限词悬空）
    s = re.sub(
        r"\b(?:with|at|in|on|near|beside|from|toward|facing|opposite)\s+"
        r"(?:his|her|their|a|an|the|its)\s*,", ",", s,
    )
    s = re.sub(r",\s*,+", ",", s)  # 连续逗号合并
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",\s*\.", ".", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().strip(",").strip()


# 道具：白底单品图，排除人物（原描述可能带"手持/胸口/佩戴"等人物成分，
# 扩写已删人物，负面词双保险防止模型画出人/手/身体）；追加排除支撑物/台座/
# 平台/阴影，确保输出只有孤立悬浮的道具本体（2026-08-10 用户反馈道具图出现
# 平台/底座与渐变背景，flood-fill 染不干净 → 从 prompt/负面词源头压制）
_NEGATIVE_PROP = (
    "worst quality, low quality, lowres, blurry, bad anatomy, bad hands, "
    "missing fingers, extra digits, deformed hands, distorted face, "
    "watermark, text, signature, duplicate, "
    "person, people, human, character, figure, crowd, face, portrait, extra person, "
    "hand, hands, arm, arms, body, torso, fingers, "
    "platform, pedestal, base, stand, holder, bracket, rack, shelf, tray, "
    "table, desk, ground, floor, stone pedestal, rock, pillar, column, "
    "shadow, cast shadow, drop shadow, reflection, mirror image, "
    "gray background, grey background, blue background, beige background, "
    "gradient background, studio backdrop, backdrop, vignette, "
    "background, scenery, environment, landscape, nature, city, room, "
    "indoor, outdoor, street, forest, sky, mountain, building, setting, scene"
)
_TYPE_NEGATIVE: dict[AssetType, str | None] = {
    AssetType.character: _NEGATIVE_BG,
    AssetType.prop: _NEGATIVE_PROP,  # 道具排除人物，保留白底单品语义
    AssetType.scene: _NEGATIVE_SCENE,  # 场景排除人物，保留背景
}

# 质量修饰段（2026-08-12 角色出图"丑"修复，开源社区最佳实践）：
# 生图 prompt 只有构图/背景指令、无正面质量词时，模型输出平庸/低质量画面。
# 社区共识：prompt 需含质量修饰词（masterpiece/best quality/highly detailed/
# sharp focus/8k uhd）+ 容貌美学词（handsome/beautiful/refined features）。
# 角色/道具/场景统一追加，仅角色额外加容貌美学段。
# 质量修饰段（2026-08-12 角色出图"丑"修复；2026-09 改为自然语言句——Z-Image 系
# Qwen-Image/S3-DiT 单流 DiT 强语言理解，官方最佳实践是完整句描述，而非逗号标签堆砌
#（旧 masterpiece/best quality/8k uhd 等标签对 Z-Image 反而减分）。用词保持风格中立，
# 避免与动漫/水墨等非写实风格冲突。
_QUALITY_BOOST = (
    "A highly detailed image with sharp focus, ultra high definition and fine intricate "
    "detail, clean crisp lines, accurate color and tonal gradation, refined craftsmanship quality."
)
# 角色容貌美学段：影视角色按审美塑造——五官精致立体、容貌出众、表情自然。
# 2026-08-12 修正"巨头"问题：上一版堆砌面部聚焦词（jawline/hairstyle/eyes）权重过高，
# 引导模型放大头部/脸部、破坏全身比例。精简为少量美学词 + 显式正常比例约束，
# 美学词必须与全身约束并存（头部大小比例正常、不放大头部）。
_CHAR_BEAUTY_BOOST = (
    "handsome attractive appearance, refined facial features, "
    # 2026-09：clear smooth skin 会引导"磨皮/无毛孔"的假脸（用户反馈无真人质感）。
    # 改为真实皮肤纹理——有毛孔/自然细节、非美颜滤镜感。
    "natural real skin texture with visible pores and realistic skin fine detail, "
    "natural confident expression, "
    "full body with natural human proportions, normal head-to-body ratio, "
    # 2026-08-18 强化的真实比例约束：无论成人/儿童，一律真实人类身体比例，
    # 绝非 Q版/卡漫比例（防"大头娃娃"）。儿童按真实儿童比例（约 4~5 头身，
    # 头部略大但远非 Q版卡漫的 2~3 头身），符合写实与 3D 半写实要求。
    "realistic human body proportions, anatomically correct human figure, "
    "normal adult or child proportions according to the character's age, "
    "child but with realistic child proportions, around 4 to 5 heads tall for a child, "
    "NOT chibi, NOT cartoon proportion, NOT oversized head, "
    "head size proportionate to body, do not enlarge the head or face, "
    "natural realistic body proportions, athletic fit figure"
)
# 写实角色封面审美 LoRA（2026-09 东方审美接入）：Z-Image-Turbo 底模专属 Realism LoRA，
# 已装在 166 D:\ComfyUI\models\loras\，与 _TXT2IMG_TEMPLATE 的 LoraLoaderModelOnly 配套。
# 仅写实角色封面由 opts.lora_name 传入启用；非写实风格与 zimg_base 走 provider 侧自动跳过。
_CHAR_BEAUTY_LORA = "Z-Image-Turbo-Realism-LoRA.safetensors"
# 场景真实感强化段（2026-08-18）：写实风格场景图此前只有通用质量词
#（masterpiece/8k uhd 偏概念图措辞），缺摄影真实感词 → 真实感全靠运气。
# 写实摄影类场景追加摄影质感词（自然光/真实材质/景深/色调），与
# _TYPE_PREFIX_SCENE_PHOTO 前缀双保险。非写实风格（动漫/水墨/3D 等）不追加，
# 避免与所选风格冲突。
_SCENE_REALISM_BOOST = (
    "photorealistic photography, ultra realistic, natural daylight, "
    "realistic materials and surface textures, accurate natural tones, "
    "realistic tonal grading, natural depth of field, "
    "realistic atmospheric perspective, physically accurate lighting, "
    "high dynamic range, cinematic realism, "
    "national geographic style, shot on real camera"
)

# 道具扩写描述清洗：删掉已缓存扩写里可能残留的支撑物/平台/底座词
# （2026-08-10：旧扩写如 "on a simple white circular platform" 会引导模型画台座，
#  新扩写 prompt 已禁止支撑物，此处兜底清洗旧缓存——用户重新生成无需重扩写）。
_PROP_SUPPORT_TERMS = re.compile(
    r"\b(platform|pedestal|stand|holder|bracket|rack|shelf|tray|table|desk|"
    r"ground|floor|rock|pillar|column|circular base|stone base|wooden base)\b",
    re.IGNORECASE,
)
# 道具描述中的人物/手持成分剥离（2026-08-18）：旧缓存或原始描述可能含
# "爸爸端着的茶杯 / 妈妈手持的红笔 / 被孩子藏在身后" 等人物行为成分——"
# held in hand/holding" 这类正面词会被模型画出人手（道具图混入人物/手）。
# 与支撑物清洗同法兜底。用 \b 边界避免误伤 handcrafted/handle/handmade；
# 用 "worn by" 短语而非 "worn"（防"做旧磨损 look" 的 worn 被误删）。
_PROP_PERSON_TERMS = re.compile(
    r"\b(people|person|persons|mother|father|mom|dad|woman|man|women|men|"
    r"boy|girl|child|children|hand|hands|held|holding|holds|hold|"
    r"carried|carries|carrying|gripped|wielded|equipped|worn by|"
    r"in (his|her|their) hand|by (his|her|their) side|on (his|her|their) body|"
    r"held by|held at|held on|picked up|picks up|"
    r"sitting on|sitting|sits on|placed on)\b",
    re.IGNORECASE,
)


def _clean_prop_prompt(p: str) -> str:
    """道具扩写描述清洗：删除支撑物/平台/底座/人物手持成分，保留道具外观。"""
    # 整段删除 "on a simple white circular platform" 这类短语（含多个前置修饰词），
    # 避免残留 "on a ... circular" 残句误导模型
    s = re.sub(
        r"\b(?:on|upon|atop)\s+(?:a|an|the|its)?\s*(?:\w+\s*){0,6}(?:platform|pedestal|"
        r"stand|holder|bracket|rack|shelf|tray|table|desk|ground|floor|rock|pillar|column)\b",
        " ", p, flags=re.IGNORECASE,
    )
    s = _PROP_SUPPORT_TERMS.sub(" ", s)  # 残留的独立支撑词
    s = _PROP_PERSON_TERMS.sub(" ", s)  # 2026-08-18：人物/手持成分剥离
    # 删除悬空所有格（father's → 删 father 留 's；' 是非单词字符，开头不能有 \b）
    s = re.sub(r"\w*['’]s\b", "", s)
    s = re.sub(r"\bthe\s+(?:while|when)\b", "", s)  # "the mother holds while" → 悬空 "the while"
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r",\s*,+", ",", s)  # 连续逗号合并
    s = re.sub(r",\s*\.", ".", s)  # 逗号+句号 → 句号
    return s.strip().strip(",").strip()


# 场景描述中的人物/活动词剥离（2026-08-18）：场景封面必须无人（硬约束 + 负面词
# 已有），但若扩写描述本身含"一家人围坐/辅导作业/母亲咆哮"等人物活动正面词，
# 模型仍可能画人（正面词权重大，IMPORTANT "ignore it" 压不住）。与角色/道具
# 清洗同法：剥离人物及人物动作词，只留环境本体。
# 词表避开歧义词以不误伤场景语义：不用 character/figure（场景里作"特色/结构"义）；
# 不用 sitting（"sitting room 起居室"）与 standing（"standing lamp 落地灯"）；
# 不用 gathering（"gathering dust 积灰"）。
_SCENE_PEOPLE_TERMS = re.compile(
    r"\b(people|person|persons|crowd|family|families|child|children|kids|"
    r"parents|mother|father|dad|mom|woman|man|women|men|students|kids out|"
    r"seated|seated at|sitting at|dining|eating|playing|studying|tutoring|"
    r"arguing|fighting|watching|waited|waiting|gathered around)\b",
    re.IGNORECASE,
)


def _strip_scene_people(text: str) -> str:
    """剥离场景描述中的人物/人物活动词，只保留环境与陈设本体。

    仅用于场景封面（空环境设定图）；分镜级 prompt 不走此清洗——分镜里的人物
    由剧情需要承载。与 IMPORTANT 无人硬约束双保险：IMPORTANT 是"指令"，
    此处是"删词"（删词对正面描述权重更有效）。
    """
    s = _SCENE_PEOPLE_TERMS.sub(" ", text)
    # 清理删除点留下的句法残渣（与 _strip_crouching_pose 同法）
    s = re.sub(
        r"\b(?:with|at|in|on|near|beside|from|toward|under|by|around|of)\s+"
        r"(?:his|her|their|a|an|the|its)\s*,", ",", s,
    )
    s = re.sub(r"\b(?:a|an)\s+the\b", "the", s)  # 冠词悬空（a the → the，容忍删词遗留多空格）
    s = re.sub(r",\s*,+", ",", s)  # 连续逗号合并
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",\s*\.", ".", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().strip(",").strip()



@celery_app.task(name="generate_asset_cover", bind=True)
def generate_asset_cover(self, task_id: str):
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
        model = db.get(Model, task.model_id)
        provider = ProviderRegistry.for_model(model)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        asset.status = MediaStatus.running
        db.commit()

        # 封面生成前自动扩写：中文描述直喂 FLUX/Wan 等英文模型会导致结果与描述
        # 完全不匹配（T5 编码器对中文理解差）。扩写结果缓存到 expanded_description，
        # 后续四视图/关键帧/视频复用，无需用户手动点"描述扩写"。
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

        # 解析项目风格（P4：风格一致；P8：未选风格时系统兜底默认写实）
        from app.models.project import Project
        from app.services.style_service import (
            get_effective_style_prompt,
            is_3d_cg_style,
            is_realistic_style,
        )
        project = db.get(Project, asset.project_id) if asset.project_id else None
        style_prompt = get_effective_style_prompt(db, project)
        # 角色/道具封面是白底孤立主体：剥离风格中的环境/场景词，
        # 防止"废墟/战场/风沙"等词引导模型画出背景（2026-08-10 修复）
        if asset.type in (AssetType.character, AssetType.prop):
            style_prompt = _clean_style_for_isolated(style_prompt)

        # 优先用扩写后的英文描述，否则用原始描述
        base_prompt = asset.expanded_description or asset.description or asset.name
        # 2026-08-11：角色封面是常态设定图——剥离扩写描述中的技能/变身/神通状态词
        #（法天象地虚影/半透明/幻影/剪影等），否则模型会把虚影/无头人形画进封面，
        # 产出"正背面服装展示图"+ 幽灵化人物。技能状态由关键帧/视频分镜描述承载。
        # 2026-08-12：角色封面同时剥离服装破损/磨损词（torn/ripped/frayed 等），
        # 旧扩写可能把"袖口磨白"放大为重度磨损，生图模型会渲染成服装撕裂。
        if asset.type == AssetType.character:
            base_prompt = _strip_skill_state(base_prompt)
            from app.services.asset_service import strip_cloth_damage
            base_prompt = strip_cloth_damage(base_prompt)
            # 2026-08-18：剥离非站姿行为词（防"下蹲"封面）——旧缓存扩写可能把
            # "蹲下/与孩子平视"写成 crouching/knees bent/eyes level with a child，
            # 生图模型会把正面蹲姿画进封面。封面是常态设定图，只保留外观。
            base_prompt = _strip_crouching_pose(base_prompt)
        # 场景封面必须无人：剥离描述中的人物/活动词（2026-08-18，删词兜底），
        # 再追加 IMPORTANT 硬约束（描述指令权重过大时，删词比 "ignore it" 有效）。
        if asset.type == AssetType.scene:
            base_prompt = _strip_scene_people(base_prompt)
            base_prompt = (
                f"{base_prompt}\n\n"
                "IMPORTANT: This is an EMPTY environment scene. NO people, NO human "
                "figures, NO silhouettes, NO crowds, NO faces anywhere in the image. "
                "Even if the description above mentions people, ignore it — depict "
                "the empty environment only."
            )
        # 角色封面必须正面全身单人（2026-08-09 修复）：扩写描述全文只写头/脸/服装
        # 细节（无腿脚内容），模型依据描述构图会生成半身/膝盖以上 → 描述后追加
        # 全身硬约束（与 scene 同法：描述指令权重大，前缀压不住）。
        # 2026-08-18：强化站姿 + 正常头身比——封面必须是"正面全身站立照"：
        # 下蹲/坐/跪/躺 一律禁止（负面词双保险，见 _NEGATIVE_BG）；
        # 头部比例正常、不放大头部（防"大头"）。
        elif asset.type == AssetType.character:
            base_prompt = (
                f"{base_prompt}\n\n"
                "IMPORTANT: This is a FULL-BODY character reference image. Show the "
                "ENTIRE character from the top of the head to the feet: complete legs, "
                "shoes and feet clearly visible at the bottom of the frame. "
                "The character stands upright in a front-facing pose, gaze directed slightly off-camera, front view, "
                "standing straight with both legs naturally extended and both feet "
                "flat on the ground — the character MUST be in a standing position, "
                "NEVER crouching, squatting, sitting, kneeling or lying down. "
                "Single character only — no one else, no extra people, no duplicates. "
                "Full body must be fully inside the frame with clear margin below the "
                "feet — do NOT crop at the waist, thighs or knees, do NOT zoom in, "
                "do NOT show only the upper body or a close-up. "
                "Keep a realistic human head-to-body ratio with a normal-sized head — "
                "do NOT enlarge the head or face, do not make the head oversized "
                "relative to the body.\n\n"
                "LIGHTING: natural balanced studio exposure, soft diffuse lighting, "
                "moderate brightness — do NOT overexpose. Preserve visible shadows, "
                "fabric folds and texture details on the costume and armor. Matte "
                "surfaces, no blown-out highlights, no pure-white clothing or skin "
                "patches. The character keeps clear tonal contrast against the light "
                "grey background."
            )
        # 道具封面必须只有道具本体（2026-08-10 修复）：旧扩写可能残留
        # "on a ... platform" 等支撑物词，引导模型画出台座/底座 → 清洗删除；
        # 描述后追加纯白底+孤立悬浮硬约束（与 scene 同法：描述指令权重大）
        elif asset.type == AssetType.prop:
            base_prompt = _clean_prop_prompt(base_prompt)
            base_prompt = (
                f"{base_prompt}\n\n"
                "IMPORTANT: This is a SINGLE isolated prop reference image on a "
                "PURE WHITE background. Show ONLY the prop itself, floating with no "
                "platform, no pedestal, no stand, no table, no shadow and no "
                "background scenery — even if the description above mentions a "
                "pedestal or platform, ignore it. No hands, no person, no other objects.\n\n"
                "BACKGROUND: the backdrop must be a clean, uniform, pure white "
                "#FFFFFF with NO gradient, NO gray/blue tint, NO studio backdrop, "
                "NO vignette — the prop must stand out with clear tonal contrast "
                "against the white background."
            )
        # 按 AssetType 构建白底/全身指令前缀（角色道具白底，场景保留环境）
        # 前缀放 prompt 开头权重最大，确保白底全身指令不被角色描述淹没
        # P4.1：风格片段提升到描述之前（风格决定整体观感，权重应高于主体描述）
        # 2026-08-09：非写实风格（动漫等）用动漫措辞 + 干净浅色底，避免
        # "photorealistic photograph + pure white" 与风格矛盾生成怪异底色
        # 2026-08-10：Z-Image base（Qwen-Image）会把 prompt 里残留的中文渲染成画面文字
        #（实测道具图出现 "2026-08-10 用户反馈…" 提示词文字）→ 组装前统一剥离中文字
        type_prefix = _TYPE_PREFIX.get(asset.type, "")
        # 2026-08-18：写实摄影类场景 → 摄影措辞前缀（放开头权重最大，防真实感缺失）
        if asset.type == AssetType.scene and is_realistic_style(db, project):
            type_prefix = _TYPE_PREFIX_SCENE_PHOTO
        # 2026-08-11：三态区分——写实 / 3D CG / 动漫插画，各自用专属措辞。
        # 旧逻辑只有"写实 / 非写实"二态，"3D半写实"被误归非写实 → 走
        # _TYPE_PREFIX_NON_REALISTIC 的 "anime character" 措辞，封面变动漫风。
        elif asset.type in _TYPE_PREFIX_3D and is_3d_cg_style(db, project):
            type_prefix = _TYPE_PREFIX_3D[asset.type]
        elif asset.type in _TYPE_PREFIX_NON_REALISTIC and not is_realistic_style(db, project):
            type_prefix = _TYPE_PREFIX_NON_REALISTIC[asset.type]
        # 剥离 prompt 中的所有中文字符（扩写描述/风格片段里的中文专名等），
        # 防止 Z-Image 原生中文文字渲染能力把提示词画进图里
        base_prompt = _strip_cjk(base_prompt)
        # 2026-08-18：清洗/去中文后主体为空（扩写缺失且描述纯中文）→ 保底用名称，
        # 避免空主体让模型全靠前缀自由发挥（道具图实测扩展后 prompt 丢失主体）。
        if not base_prompt.strip():
            base_prompt = asset.name or ""
            if asset.name:
                logger.warning(
                    "资产 %s（%s）生图 prompt 主体为空（扩写缺失/描述为纯中文），回退为名称",
                    asset.id, asset.name,
                )
        parts: list[str] = []
        if type_prefix:
            parts.append(type_prefix)
        if style_prompt:
            parts.append(f"Art style: {_strip_cjk(style_prompt)}")
        parts.append(base_prompt)
        # 2026-08-12：质量修饰词 + 角色容貌美学段（社区最佳实践），放描述之后。
        # 角色封面追加容貌美学词，保证出图"好看"；道具/场景只加质量词。
        # 2026-08-18：写实摄影类场景另追加强真实感词（_SCENE_REALISM_BOOST）。
        boost_parts = [_QUALITY_BOOST]
        if asset.type == AssetType.character:
            boost_parts.append(_CHAR_BEAUTY_BOOST)
        elif asset.type == AssetType.scene and is_realistic_style(db, project):
            boost_parts.append(_SCENE_REALISM_BOOST)
        parts.append(" ".join(boost_parts))
        prompt = "\n\n".join(parts)
        negative = _TYPE_NEGATIVE.get(asset.type)
        # 写实摄影类风格场景图：追加"非真实感排除"负面词（2026-08-18 修复）。
        # 旧 _NEGATIVE_SCENE 只排除人物、不含 cartoon/illustration/3d render/
        # painting 等非真实感词——写实场景可自由漂移到插画/3D/油画质感而负面词
        # 拦不住，是"缺真实感"的主要漏洞。与角色图同法：仅写实风格追加，
        # 避免与所选风格冲突。
        if negative and asset.type == AssetType.scene and is_realistic_style(db, project):
            negative = (
                f"{negative}, anime, cartoon, illustration, 3d render, cg render, "
                "digital art, painting, drawing, stylized, non-realistic, "
                "flat colors, flat lighting, video game cg, cel shading, "
                "watercolor, sketch, oil painting, toon, claymation, "
                "overexposed, bleached, plastic look, toy-like, "
                "fake, waxy, glossy plastic, low dynamic range"
            )
        # 真人写实类风格（写实摄影/电影感/黑白/未显式选风格的兜底写实）：
        # 角色追加"非真人排除"词，防止动漫/3D/插画混出；
        # 非写实风格（动漫/水墨等）不追加，避免与所选风格冲突
        elif negative and asset.type == AssetType.character and is_realistic_style(db, project):
            negative = (
                f"{negative}, anime, cartoon, illustration, 3d render, cg render, "
                "digital art, painting, drawing, stylized, non-realistic, "
                # 2026-09：反磨皮/反滤镜 (无真人质感的"假脸") + 反画感/反噪点 (像油画/带噪点)
                "airbrushed, plastic skin, porcelain skin, doll-like, beauty filter, "
                "over-smoothed skin, over-beautified, flawless skin, waxy skin, "
                "film grain, noise, grainy, canvas texture, brush strokes, "
                # 东方古风角色排除西方元素（2026-08-10 用户反馈女主变中世纪骑士）
                "western armor, european knight, plate mail armor, chainmail, "
                "medieval armor, western style, european style, viking, "
                "samurai, ninja, japanese style, western braid hairstyle, "
                "knight helmet, gothic, renaissance, baroque"
            )
        # 3D CG 类风格（3D半写实/3D渲染）：排除动漫/插画/2D 元素，
        # 但保留 "3d render"（那是目标风格本身），排除词与写实类不同。
        elif negative and asset.type == AssetType.character and is_3d_cg_style(db, project):
            negative = (
                f"{negative}, anime, anime style, cartoon, cel shading, 2d animation, "
                "flat 2d illustration, manga, comic, watercolor, oil painting, "
                # 东方古风角色排除西方元素（同上）
                "western armor, european knight, plate mail armor, chainmail, "
                "medieval armor, western style, european style, viking, "
                "samurai, ninja, japanese style, western braid hairstyle, "
                "knight helmet, gothic, renaissance, baroque"
            )
        # 资产图是设定参考图，不跟随项目比例：
        # - 角色/道具用 1:1 方形，稳定容纳全身/单品，避免窄竖屏裁掉头脚
        # - 场景保留项目比例，环境构图与成片一致
        # - 角色/道具封面走 1024 档（1024×1024 原生最优，放大查看无像素感）；
        #   场景封面保持 768p 基准与视频对齐（opts.base 仅对 1:1 生效）
        # 成片比例由关键帧/视频生成的 aspect_ratio 控制
        if asset.type == AssetType.scene:
            ratio = project.aspect_ratio if project else None
            opts = ImageOpts(ratio=ratio, size="2K", negative_prompt=negative)
        else:
            ratio = "1:1"
            opts = ImageOpts(
                ratio=ratio, size="2K", negative_prompt=negative, base=1024,
                # 2026-09：仅写实角色封面挂审美 LoRA（提升美观度/真人质感）；道具/非写实不挂，
                # 避免与白底单品语义或所选风格冲突；zimg_base 由 provider 侧按 capability 自动跳过。
                lora_name=(_CHAR_BEAUTY_LORA
                          if (asset.type == AssetType.character and is_realistic_style(db, project))
                          else None),
                lora_strength=1.0,
            )
        # 审核拦截（content_policy_violation）为 Agnes 间歇性误判，同 prompt 重试可自愈
        for attempt in range(_POLICY_MAX_RETRIES):
            try:
                handle = provider.textToImage(prompt, opts)
                break
            except Exception as e:
                if is_content_policy(e) and attempt < _POLICY_MAX_RETRIES - 1:
                    logger.warning(
                        "资产 %s 封面被安全策略拦截（第 %s/%s 次），重试中…", asset.id,
                        attempt + 1, _POLICY_MAX_RETRIES,
                    )
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
        update_task(
            db, task_id, provider=handle.provider,
            provider_task_id=handle.providerTaskId, poll_url=handle.pollUrl, progress=10,
        )

        result = run_with_polling(db, task_id, provider, handle, poll_interval=2, timeout=600)
        local_url = download_to_local(
            result.imageUrls[0], subdir=f"assets/{asset.id}", filename="cover.png",
            task_id=task_id,
        )
        # 2026-08-10：封面图原样使用 ComfyUI 服务器原图，不做任何像素级后处理
        # （不染底色、不压高光、不注入 ICC）。底色统一通过 prompt 提示词实现。
        # 用户明确反馈 ComfyUI 原图无问题，任何后处理都会引入失真。
        asset.cover_url = local_url
        # 方案A：记录生成该资产时的项目生效风格指纹（全局复用时做一致性判断）
        try:
            from app.services.asset_service import snapshot_style_fingerprint
            asset.style_fingerprint = snapshot_style_fingerprint(db, project)
        except Exception:
            pass
        asset.status = MediaStatus.succeeded
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=local_url, finished_at=now(),
        )
        db.commit()
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
