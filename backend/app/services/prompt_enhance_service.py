"""提示词增强管线（P4）：分镜信息 → LLM 精细英文生图/生视频 prompt + 负面词。

核心 API：
    ensure_enhanced_prompt(db, segment, project, target="image", force=False)
        -> tuple[str, str]  # (prompt, negative_prompt)

流程：
1. 缓存命中（segment.enhanced_prompt 非空且非 force）→ 直接返回缓存
2. 未命中 → 收集上下文（中文描述/景别/运镜/情绪/角色/场景/道具/风格）
3. 调 LLM 生成结构化英文 prompt + negative（JSON 输出，失败重试 2 次）
4. 写回 segment 缓存并 commit → 返回

容错：LLM 失败/解析失败不阻断生成链路，回退为「原始描述 + 内置负面词」。
"""
import json
import logging
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.model_config import Model, ModelType
from app.models.project import Project
from app.models.segment import Segment
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.shot_beats import beats_fingerprint, format_beats_block

logger = logging.getLogger(__name__)

# 内置负面词兜底：LLM 失败或未返回负面词时使用（覆盖常见畸形/低质问题）
_FALLBACK_NEGATIVE = (
    "(worst quality, low quality, normal quality:1.4), lowres, bad anatomy, "
    "bad hands, missing fingers, extra digits, extra fingers, deformed hands, "
    "fused fingers, malformed limbs, distorted face, blurry, jpeg artifacts, "
    "watermark, signature, text, logo, duplicate, cropped, "
    "out of frame, bad proportions, disfigured, ugly, cross-eyed"
)

# 单人镜专属负面词：`multiple people` 只在该镜确定为单人主体时追加。
# 群像/对峙/多人同框镜若用它反而会抑制本应出现的多人（与六段式「角色数量与描述一致」
# 的正向约束冲突），故从通用负面词中拆出、按镜条件追加（2026-08-31）。
_SINGLE_SUBJECT_NEGATIVE = "multiple people"

# 增强模板版本：修改 _ENHANCE_TMPL / _ENHANCE_VIDEO_TMPL（含其中任何一条规则/硬约束）后
# 手动 bump，并纳入增强缓存键（见 ensure_enhanced_prompt 的 _v 后缀）——
# 否则改了模板规则后，已生成分镜的 enhanced_target 不变、仍命中旧缓存，用户沿用旧规则出片。
_TEMPLATE_VERSION = "v6"

# 视频 AI 化负面词（对抗"AI 味"）：塑料/蜡像皮肤、CGI/3D 动画感、卡通描边、
# 过度平滑、表情僵硬、运动不连贯、肢体扭曲、形变伪影、面部崩坏。视频生成时追加到负面词。
# 2026-08-10：增补 AI 生成感/空洞感（ai slop、uncanny valley、soulless）对抗"假人"观感。
_VIDEO_AI_NEGATIVES = (
    "plastic skin, waxy face, cgi render, 3d animation, cel shading, cartoon style, "
    "doll-like, airbrushed, oversmoothed skin, frozen expression, stiff motion, "
    "warped limbs, morphing artifacts, warped face, distorted face, deformed face, "
    "facial distortion, face morphing, melting face, flickering face, unstable face, "
    "disfigured face, cross-eyed, misplaced facial features, artificial glare, "
    "ai slop, uncanny valley, soulless, lifeless eyes, blank expression, "
    "glossy cgi look, plastic look, "
    "harsh lighting, overexposed, blown-out highlights, pure-white skin patch, "
    "specular glare, glossy specular highlights, airbrushed skin, waxy plastic skin, "
    "overexposed face, blown-out face, pure-white face, white face patches, "
    "harsh backlight on face, burned-out highlights on skin, clipped highlights"
)

# 视频 AI 味负面词中与「动漫/3D CG/插画/水墨/国风」等**非写实**目标风格直接冲突的词。
# 写实/电影/仿真人风格全量保留（对抗塑料/蜡像/CGI 假人感）；而选动漫/3D/插画等风格时，
# 这些词会直接否掉目标风格（如 cel shading/cartoon style/3d animation/glossy cgi look），
# 造成风格表达打折扣，须从负面词中剔除。见 _video_ai_negatives_for_style。
# 2026-08-31：依据 style_service.is_realistic_style 裁剪（与资产生成追加"非真人排除"负面词的判定一致）。
_VIDEO_AI_STYLE_CONFLICT = (
    "cel shading", "cartoon style", "3d animation", "glossy cgi look",
    "cgi render", "ai slop", "plastic look", "doll-like",
)


def _video_ai_negatives_for_style(db, project) -> str:
    """按项目有效风格裁剪视频 AI 味负面词，避免与目标风格冲突。

    - 写实/电影/仿真人 → 全量 _VIDEO_AI_NEGATIVES（对抗塑料/蜡像/CGI 假人感）；
    - 动漫/3D CG/插画/水墨/国风等非写实 → 剔除与目标风格冲突的词
      （cel shading / cartoon style / 3d animation / glossy cgi look / ai slop 等），
      仅保留人体结构/表情/形变类负面（plastic skin、warped limbs、facial distortion 等）。
    - project=None 或风格未知 → 按写实兜底全量保留。
    """
    from app.services.style_service import is_realistic_style

    realistic = is_realistic_style(db, project)
    if realistic:
        return _VIDEO_AI_NEGATIVES
    keep: list[str] = []
    for tok in _VIDEO_AI_NEGATIVES.split(","):
        t = tok.strip()
        if not t:
            continue
        if any(conf in t for conf in _VIDEO_AI_STYLE_CONFLICT):
            continue
        keep.append(t)
    return ", ".join(keep) if keep else _VIDEO_AI_NEGATIVES

# 景别 → 英文镜头语言
_SHOT_TYPE_EN = {
    "远景": "establishing wide shot",
    "全景": "wide shot",
    "中景": "medium shot",
    "近景": "close-up shot",
    "特写": "extreme close-up",
    "远景/全景": "wide establishing shot",
}
# 运镜 → 英文镜头运动
_CAMERA_EN = {
    "固定": "static camera",
    "推": "slow push in",
    "拉": "slow pull back",
    "摇": "pan",
    "移": "dolly tracking shot",
    "跟": "follow shot",
}

# 画面含主体位移/入场/开关门等动作的常见动词——用于「无显式运镜时的运镜兜底」判定。
# 2026-08-28：只做视频端兜底；显式写「固定」仍尊重作者意图（真·静止）。
_VIDEO_MOTION_VERBS = (
    "走进", "走出", "走入", "走过来", "走过去", "走到", "走向", "跑来", "跑向", "入画",
    "转身", "回头", "推门", "开门", "进门", "出门", "出电梯", "进电梯", "下车", "上车",
    "靠近", "迎向", "离开", "迈步", "踱步", "缓步", "疾步", "转身离开", "侧身", "搬",
    "端", "捧", "递", "拾", "弯腰", "蹲下", "站起", "起身", "推着", "拖着",
)


def _video_camera_hint(segment) -> str:
    """视频运镜兜底：分镜未显式写运镜时，依据画面动作推断镜头运动，避免整镜静止。

    规则（与 llm_script_service 的运镜默认一致：对话文戏=固定或缓慢推）：
    - 显式写了运镜（含「固定」）→ 原样尊重，不覆盖作者意图；
    - 运镜为空 + 画面出现主体位移/入场/开门等动作词 → 默认「缓慢推近」，让镜头有呼吸感；
    - 运镜为空 + 纯静态画面（无人/物动作）→ 保持空（硬造运镜反而怪）。
    """
    camera = (segment.camera or "").strip()
    if camera:
        return camera
    desc = (segment.description or "").strip()
    if not desc:
        return ""
    if any(v in desc for v in _VIDEO_MOTION_VERBS):
        return "缓慢推近"
    return ""

def _continuity_block(db: Session, segment) -> str:
    """跨镜一致性硬约束：注入上一镜的延续信息，避免镜头间跳变（P1-3）。

    上一镜 = 同幕、index-1 的分镜。仅当存在时返回延续约束块；首镜或
    无上一镜时返回空串。约束内容：延续光照色调/情绪氛围、在场角色外观
    与站位、影调风格，避免镜头间跳变。
    """
    prev = None
    segs = getattr(getattr(segment, "episode", None), "segments", None)
    if segs is not None:
        for s in segs:
            if s.index == (segment.index - 1):
                prev = s
                break
    if prev is None:
        return ""
    camera = prev.camera or ""
    emotion = prev.emotion or ""
    # 上一镜是否含明确主体位移/出场等动作（用于占位延续判断）
    desc = prev.description or ""
    motion = "有主体移动/入场" if any(v in desc for v in _VIDEO_MOTION_VERBS) else "静态"
    lines = [
        "\n\n【跨镜连续性（硬约束，P1-3）】本镜是上一镜的延续，生成时必须以视觉连续性为硬约束：",
        "- 延续上一镜的光照色调与情绪氛围（上一镜情绪=" + (emotion or "无") + "），避免镜头间色调跳变；",
        "- 保持上一镜所有在场角色的外观完全一致（同一角色、同一服装、同一发型），不得改变形象；",
        "- 若画面延续上一镜的主体动作/站位，保持空间位置与朝向连贯，衔接自然；",
        "- 延续上一镜的影调与运镜风格，镜头切换平滑，不做突兀的风格突变。",
        "（上一镜参考：景别=" + (prev.shot_type or "无") + "，运镜=" + (camera or "无") + "，" + motion + "）",
    ]
    return "\n".join(lines)


_ENHANCE_TMPL = """你是一名电影级 AI 提示词工程师。根据分镜信息，把中文画面描述扩写为一条精细的{target_label}提示词，并给出负面提示词。

【分镜信息】
- 画面描述（中文）：{description}
- 景别：{shot_type}
- 运镜：{camera}
- 情绪氛围：{emotion}
- 对白/旁白：{dialogue_block}
{assets_block}

【返回格式】
只返回纯 JSON（不要 markdown 代码块、不要任何解释文字），结构：
{{"prompt": "精细提示词", "negative_prompt": "负面提示词"}}

【prompt 编写总要求】（Z-Image Turbo 最佳实践）
- 分层描述法：按「主体→外观与手持物品→动作姿态→环境背景→光照氛围→（特殊视觉特效）」逐层细化，模块化表达避免信息混杂
- 完整主谓宾句式：使用自然语言完整句描述，禁止碎片化标签堆砌（Z-Image 基于 S3-DiT 单流 DiT，强语言理解，完整句式一致性显著优于关键词堆叠）
- 限定词增强精确性：用具体颜色/材质/数量/位置副词约束（如 bright yellow glow、silk fabric、three birds、above her left palm），减少随机性
- 语序调控权重：关键元素前置（句首权重最高）；重点特征可自然重复一次（不同语境，非机械堆叠）
- 多主体构图：出现多个主体时用方位词+互动动词区分（如 one crouching to catch a fish, the other laughing and pointing），并用 foreground/middle ground/background 分层
- 自然文字渲染：场景中招牌/路标/报纸/对联等自然文字，明确写出文字内容与显示方式（如 a signboard with the Chinese characters '欢迎光临' in red ink），单幅文字≤3组

【prompt 编写规则】（600 词以内）
1. 主体：{subject_rule}
2. 动作/姿态与质感：{action_rule}
3. 构图：景别翻译为镜头语言（远景=establishing wide shot、全景=wide shot、中景=medium shot、近景=close-up shot、特写=extreme close-up）；按景别写清主体在画面中的位置关系与画幅留白，保持主体居中平衡、画面留白合理
4. 场景：结合场景描述写环境与氛围
5. 光照与色调（电影级但**务必控曝光**）：设计布光与色彩倾向——说明光源类型（自然光/路灯/霓虹/伦巴第光等）与色调分级（冷蓝夜戏、暖黄温馨、青橙对比等）；**曝光铁律：人物面部绝不过曝**——脸上不得出现死白高光/纯白斑块（no blown-out highlights / no pure-white face patches），高光为柔和自然光泽而非刺眼镜面反光，保留皮肤与衣物的明暗过渡与可感知阴影；**夜景/逆光场景**：环境光源（路灯/霓虹/灯光）可做氛围亮斑，但**照到面部的入射光必须柔和收敛**，主光加 face fill 保证五官可辨，严禁让脸背对着强光源被打成死白；避免平光（要有立体感）但**绝不爆高光**
6. 镜头语言与动作时序：{target_rule}
7. 风格（最高优先级）：必须原样包含「Art style: {style}」，并以该风格为核心组织画面——媒介、笔触/渲染方式、色彩、质感、构图全部严格遵循该风格；风格为空时按写实电影风格处理
8. {quality_tail}
9. {language_rule}；不得编造上下文中不存在的角色外貌特征
{structured_rule}

【negative_prompt 规则】
覆盖手部畸形、多余手指、肢体变形、面部崩坏、五官错位、模糊、低分辨率、噪点、文字、水印、签名、多余人物、构图裁切等，英文逗号分隔，15~30 个短语。"""

# 视频专用模板：MiniMax H3 官方 Ref2VA 六段式改写结构（h3-prompt-writing 技能 ref-en.txt 规范）。
# 2026-08-09 整合：视频增强输出按官方六段式（subject_definitions / summary /
# retention_analysis / detailed_description / overall_soundscape / non_diegetic_music），
# 字段名、段落顺序、标签、时间记法严格对齐 ref-en.txt；同时保留系统已有的
# 旁白音色固定、角色声线档案执行、简体中文硬约束。
_ENHANCE_VIDEO_TMPL = """You are a professional MiniMax H3 prompt writer. Rewrite the shot below into the official full-reference (Ref2VA) six-section format, following the H3 prompt-writing skill (references/ref-en.txt).

【正文语言（最高优先级，必须在整个六段式正文严格执行）】
{output_lang_rule}

【Shot info】
- 画面描述（中文）：{description}
- 景别：{shot_type}
- 运镜：{camera}
- 情绪氛围：{emotion}
- 对白/旁白（中文原文，供照搬进 <d>，含情绪/声线标注）：{dialogue_block}
{beats_block}
{assets_block}{ref_block}{continuity_block}

【Target】视频时长 {duration_seconds} 秒（24fps）。所有镜头切割时间必须落在 0~{duration_seconds} 秒内且严格递增。

【Output format — STRICT】
只返回纯 JSON（不要 markdown 代码块、不要解释文字），结构：
{{"prompt": "六段式完整文本(英文)", "prompt_zh": "同内容简体中文翻译版", "negative_prompt": "负面提示词"}}

【prompt_zh — STRICT】
prompt_zh 必须与 prompt 逐字段对齐的简体中文翻译：字段名行保持英文小写蛇形
（subject_definitions: / summary: / retention_analysis: / detailed_description: /
overall_soundscape: / non_diegetic_music:），标签与固定标记保持英文
（<Subject N> / <Picture N> / [Shot N] / (S1) / Art style: / At MM:SS.mmm），
正文用简体中文；<d>[Chinese] 中文原句</d> 内的台词/旁白必须与 prompt 完全一致
（逐字保留中文原句）；画面可见自然文字保留简体中文。

【正文语言（STRICT）】
{output_lang_rule}

prompt 字段必须严格按以下六段顺序书写，字段名与标签完全照抄（参考 H3 官方 ref-en.txt）：

subject_definitions:
为每条参考资产定义 <Subject N>（角色/场景/道具各一条），**必须覆盖【参考图清单】中的每一张参考图**——每张参考图都必须被至少一个 <Subject N> 引用为特征来源，不得遗漏、不得把未定义的图忽略掉；说明其来源参考图（写清 Image N）与目标视频中需保持一致的特征（外貌/服装/场景要素/材质/配色等），并写清参考角色名。
格式：`<Subject 1> is the character named 「角色名」 from Image 1, ...`。同一 Subject 可由多张参考图共同定义：`<Subject 1> is the character whose appearance comes from Image 1 and whose clothing comes from Image 2`。姿态/朝向由分镜描述决定，**不得默认写 full frontal / facing the camera**——除非分镜明确要求正面构图（如自我介绍、证件照式特写）；写姿态时优先侧面/3/4 视角或按分镜动作写清站位。
**标签贯穿性（最关键）**：后续所有段落（summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music）必须复用同一标签、保持同一含义，不得改名、不得新增未定义标签、不得自行换号或合并编号。
注意：<Subject N> 仅是**外观特征参考来源（appearance reference）**，参考图本身不是画面内容——不得在画面中展示任何参考图片、图片边框、缩略图或网格拼图。
若【参考图清单】为空（本镜无资产参考，纯文生视频）：subject_definitions 与 retention_analysis 一律写 N/A，summary 仍以 `[reference generation] ` 开头但不引用任何 <Subject N>，detailed_description 直接描述画面本体。

summary:
首行以 `[reference generation] ` 开头（若参考图同时作为具体帧/关键帧锚点，可组合任务类型，多个任务类型用 ` + ` 连接、不重复，如 `[reference generation + keyframe completion]`），用一句英文总结目标视频与各 <Subject N> 的参考关系。**只允许复用 subject_definitions 中已定义的 <Subject N> 标签，严禁在本段引入任何新标签。**

retention_analysis:
每个 <Subject N> 一行，**逐一覆盖（数量与 subject_definitions 完全一致，不遗漏、不新增）**，按实际出现的镜头标注：`<Subject N> (appears in [Shot 1], [Shot N]): fully_preserved - ...`（按需选 fully_preserved / partially_preserved / attribute_transfer / weak_reference 并说明保留了什么）。

detailed_description:
- 在 [Shot 1] 之前先用 1-2 句英文确立风格，必须原样包含「Art style: {style}」并以该风格组织全片（风格为空时按 cinematic live-action 写实电影风格）。
- 镜头标记：[Shot 1] 开头不写时间戳；后续镜头 `[Shot N] At MM:SS.mmm, the camera cuts to ...`（MM:SS.mmm 为两位分、两位秒、三位毫秒，如 `At 00:03.333`），切割时间严格递增且不超 {duration_seconds} 秒；仅当距离/角度微调时用镜头运动而非切镜。
- **节拍约束（当【节拍计划】非空时为本镜最高优先级结构要求）**：[Shot N] 数量与节拍数完全一致、顺序一一对应；[Shot 1] 描述节拍 1；后续每个 [Shot N] 的切割时间必须精确等于对应节拍的 start_sec（`At MM:SS.mmm` 与该秒数逐位对齐，如节拍 2 起于 2s → `At 00:02.000`）；每个 [Shot N] 的画面内容/景别/运镜必须与对应节拍严格一致，禁止精简合并节拍、禁止拆开调整顺序、禁止新增节拍；节拍内的景别按上文译成镜头语言、运镜按类型+幅度+速度译成英文，节拍 content（中文）展开为该时段完整镜头正文——构图/机位/站位/对白/音效规则全部照常适用于每个 [Shot N]。
- 运镜以自然英文融入句中（push in / pull out / pan right / truck left / tilt up / tracking shot / static shot 等），需要时加幅度（with small/large amplitude）与速度（at slow/fast speed）。
- **运镜兜底（【Shot info】运镜为空且画面存在主体移动/入场/开门等动作时必执行）**：本镜禁止整镜静止——默认缓慢推近（slow push in）；主体有明显位移（走出/跑过/跟随）时用轻微跟踪/横移（subtle tracking shot / slight truck），让镜头跟着动作走；只有运镜显式写「固定」时才允许 static shot。
- 说话者按实际发声顺序分配稳定 ID (S1)、(S2)…，跨镜头保持同一 ID，同一句多人齐声用 (S1,S2)。首次出现时给出简短音色描述（年龄段/性别/音色/语速）——必须贴合对话数据中「声线：」标注的档案；**有声线档案的角色，其音色描述必须显式写出性别（male=男声/female=女声），严禁只用 boy/girl、child/young 等不含性别的词或纯中性音色词替代**（如星野声线标注「男声·…」→ 必须写 male voice 或 male）；对白以 `<d>[Chinese] 中文原句</d>` 原样放入，逐字保留、绝不翻译。**<d> 内只允许放置台词原文**：说话人名字、角色名、ID、冒号、括号、声线/情绪说明等一律写在 <d> 之外，绝不进入 <d>；角色朗读时只发出 <d> 内的台词文字，**绝对禁止读出说话人名字（如「林浅」）、「S1」等标签、冒号或任何提示词标记**。**语速按「情绪氛围」匹配**：紧张/愤怒/恐惧/震惊 → 说得快而急促（in a fast, hurried pace，句间停顿短）；欢快 → 轻快活泼（in a lively, quick pace）；平静/温馨 → 自然对话语速（in a natural, conversational pace）；史诗/庄重 → 缓慢有力（in a slow, deliberate pace）——全程贴近真实人物说话节奏，避免慢悠悠朗诵腔。
- **说话者若为被引用的参考角色**（在 subject_definitions 中已定义 <Subject N>），写 `<Subject N> (Sx)`——同时保留视觉参考标签与说话人 ID（如 `<Subject 1> (S1) turns and says, <d>[Chinese] ...</d>`）。
- **参考图作为具体帧/构图锚点**：用自然措辞写（`the shot begins from <Picture 1>` / `the shot's keyframe corresponds to <Picture 2>` / `the shot ends on <Picture 3>`），并描述该帧的内容与衔接；若参考图仅作角色/场景外观特征来源（不锚定具体帧），则不用 <Picture N> 措辞，仅引用 <Subject N>。
- 开口对白：角色正常说话、对嘴型，写 `(S1) says ... <d>[Chinese] 中文原句</d>`。
- 内心独白（对话数据标「内心独白」的行）：写 `(S1) says in an inner monologue voice-over with his/her own voice <d>[Chinese] 中文原句</d> while his lips remain completely closed`——用角色本人声音、画外音形式，画面中该角色嘴唇必须紧闭、不得对口型念出内心独白。
- 旁白/画外音：写 `says in an off-screen voiceover: <d>[Chinese] 中文原句</d> while his lips remain completely closed`——画面中所有角色必须闭嘴，不得对口型念旁白；旁白音色固定为「{narrator_voice}」，全片保持同一音色、不得变化。
- 说话语气按「情绪氛围」与对话数据中的情绪标注写入。
- **真实感细节（本分镜有台词或动作时按需写入，避免空洞摆拍）**：写清微表情与眼神（瞳孔收缩/眉梢微挑/嘴唇紧抿/嘴角抽搐/眼眶泛红/眼神躲闪）、肢体动作的时间顺序与力度重量（先…然后…，猛地/踉跄/下意识地/强撑着）、人物之间的空间距离与互动（逼近对方、避开视线、按住对方手腕）、环境对动作的物理反应（衣摆/发丝随动作摆动、踩到落叶沙沙响、水花溅起、灰尘被带起）、以及生活化小动作（下意识扶额、抿嘴、摩挲衣角、低头看表）——所有动作服务于人物情绪，节奏自然不拖沓，避免呆立念白。
- **皮肤与光影真实感（真人/电影风格必写，最高优先级）**：人脸必须是真实真人皮肤——保留自然毛孔、细纹、微瑕疵与肤色过渡，用柔和自然的漫射光、适中对比度；**严禁**磨皮/过度平滑/塑料/蜡像脸，**严禁**局部死白高光/白斑/纯白皮肤（no blown-out highlights / no pure-white skin patch），五官与面颊要有温和明暗过渡与可感知的阴影，脸部高光为柔和自然光泽而非刺眼镜面反光（no glossy specular glare）。
- **曝光与控光（必写，最高优先级）**：**人物面部绝不过曝**——不得出现死白高光/纯白斑块（no blown-out highlights on the face / no overexposed face），保留皮肤与衣物的明暗过渡；**夜景/逆光/点光源场景**：环境光源（路灯/霓虹/灯光/月光）可做氛围亮斑与背景光斑，但**照到面部的入射光必须柔和收敛**，主光加面部补光（face fill）保证五官可辨，严禁让脸背对强光源被打成死白或大面积纯白；画面光源可亮、**照到人面的光必须压住**。
- 画面可见文字（招牌/路标等）放英文双引号内，保留中文原文不翻译。
- **机位与站位（H3 关键，必写）**：每个 [Shot N] 必须从镜头视角（camera's viewpoint）写清主体间的相对空间关系——左右/前后站位、朝向、以及前景/中景/背景分层。主体朝向与姿态由分镜描述决定，不得默认写 full frontal 或让主体正对镜头（除非分镜明确是正面构图/主观视角）。打斗/对峙类分镜默认用侧面或 3/4 机位，交战双方必须同框、彼此可视，**禁止把对手/怪兽置于主角身后被遮挡的死角构图**——作文本如「the camera sees the beast to the left, facing the protagonist, both in profile」。
- **视线朝向（关键，必写）**：每个 [Shot N] 必须写清角色**视线落点**——看向对话对象、看向动作/手持物目标、或看向场景内某处；**禁止角色视线望向镜头、与观众对视**（「looking at the camera / making eye contact with the viewer / gazing into the lens 」一律禁止），除非分镜明确是面对镜头的主观视角/直面观众的互动（如主播、vlog、四维墙前独白）。对白时角色看向说话或聆听对象，而非镜头；无对象时视线跟随自身动作方向或场景焦点。
- **位移朝向一致性（通用，必写）**：主体有明确移动方向（走进/走出/跑向/靠近/入画/离开）时，其面部朝向必须与移动方向一致（面朝前进方向），构图优先 3/4 前侧机位——画面能同时看到去路与面部。**禁止主体背对移动方向/背对出口出画**（如出电梯、出门口、推开门的瞬间，都应让观众看到面部与去路，转弯/避让可用侧前角度）；参考角色的转身/侧身要写清转向前后两次朝向。
- 环境/物理音效（脚步声、雨声、门响等）按镜头时序写入 detailed_description 对应镜头；**打斗/动作/仙法镜头必须穷举音效**：兵器金属碰撞声、破空声、沉重打击声、能量爆裂声、符文嗡鸣、衣袍/布料翻飞、蹬地/落地、尘土与气流扰动、角色发力闷哼与喘息——凡画面发生的动作都要有对应声音，禁止静音/只有对白。
- 总字数 350-500 英文词（对白密集场景优先保证完整对白时间线，可超出）。

overall_soundscape:
用 1-4 句英文（单段）总结全片环境音 + 物理动作音 + 非语言人声（风/雨/脚步/织物/呼吸/衣物摩擦等）：写清声音来源、音量大小与节奏变化，客观描述（如 steady rain tapping / low room ambience continuing underneath），**禁止使用抽象情绪词或氛围词**（如 peaceful、chaotic、quiet atmosphere、tense silence 等情绪化修饰，只写实际发出的声音事件）；对白、歌声、配乐不在此重复；仅全片静音时写 N/A。

non_diegetic_music:
用 1-3 句英文描述观众可闻、角色不可闻的背景音乐：**只写配器、速度（tempo）、节奏、力度与动态变化**（如 sparse piano notes at a slow tempo, joined by sustained low strings that gradually increase in volume before fading out），**严禁使用抽象情绪词（sad、happy、tense、uplifting 等）或解释配乐的情绪功能**；配乐音量不得压过对白人声与重要音效；角色能听见的现场音乐（收音机/电视/街头艺人）属 diegetic，应写入 detailed_description 而非本段；无配乐写 N/A。

【Hard constraints】
{scope_flags}
- 【节拍计划】非空时，必须以节拍结构优先组织 detailed_description 的全部 [Shot N]，任何其他规则（含运镜自由发挥）不得与之冲突。
- 参考资产（<Subject N>）仅作为外观/服装/场景特征提取来源，**参考图本身严禁以任何形式出现在画面中**——不得显示图片边框、缩略图、四视图网格、照片、画板、海报、截图、UI 叠层或角色图鉴式排列。
- 主体朝向与站位由分镜描述决定；打斗/对峙类分镜必须双方同框、彼此可见（侧面/3/4 机位），严禁默认 full frontal 或把对手放在主角身后被主体遮挡。
- **运镜兜底（硬约束）**：运镜为空但画面有主体移动/入场/开关门等动作时，严禁整镜静止——必须用缓慢推近/轻微跟踪等镜头运动；只有运镜显式写「固定」才可 static shot。
- **位移朝向一致性（硬约束）**：主体有移动方向（出电梯/出门/走进/跑向等）时，面部必须朝向移动方向（3/4 前侧机位），禁止背对移动方向/背对出口出画。
- **视线朝向（硬约束）**：角色视线必须有明确落点（对话对象/动作目标/场景焦点），**禁止望向镜头、与观众对视**（禁止 looking at the camera / eye contact with the viewer / gazing into the lens）；仅当分镜明确为面对镜头的主观视角/直面观众时才允许看向镜头。
- **出电梯/出门（硬约束，H3 常见误读）**：出电梯/出门类镜头机位必须在外部空间（走廊/门外）拍摄主体走出过程，主体必须离开轿厢/门内空间、迈入外部空间——**禁止主体停留在轿厢内部或门内**；轿厢/门内视角仅可用于主体仍在内部的镜头（如乘梯内景）。
- **道具交接一致性（硬约束）**：递接/交还/传递道具（手机/伞/碗/文件等）时，道具归属必须全片一致——谁持有、谁接走、何时换手，**禁止道具凭空消失、凭空出现、无来源转移**；交接过程允许轻微切景别/遮挡过渡，但持有者必须连贯。
- **屏幕文字可读（硬约束）**：手机屏幕/指示牌/LED/招牌等画面重要文字，必须渲染为简体中文且笔画清晰可读过目可辨（短信界面呈现明确的中文会话内容，数量可精简但不可糊成乱码）——**禁止韩文/日文/英文混入、禁止笔画烂糊不可读**。
- **妆容与外观稳定（硬约束）**：人物妆容必须按参考资产与分镜描述保持（素颜即素颜、裸妆即裸妆），**禁止添加参考中没有的妆容**（醒目眼影/红唇/浓妆等在描述未要求时一律不得出现），发型/发色/脸型跨镜头保持一致。
- 画面中只出现本分镜描述中实际在场的角色，**角色数量严格与分镜描述一致**——禁止额外人物、禁止参考图中其他角色乱入；**允许剧情内的多人在场**（对峙/交谈/并肩/围观等，属正常叙事），但**禁止**把在场角色拍成「展示/介绍性质」的并列图鉴式构图（多角色面向镜头一字排开、无剧情互动的画报式摆拍）；旁白镜头中若描述无人物在场则画面不得出现人物。
- 所有角色语音、旁白必须且只能用简体中文（普通话，zh-CN）说出，逐字朗读 <d> 内中文原句，绝对禁止翻译成英语或任何外语、禁止夹杂任何英文单词、禁止中英混杂。
- **朗读内容只包含 <d> 内的台词原句**：说话人名字、角色名、冒号、括号、标签（如「S1」）、声线/情绪描述等提示词元素绝对禁止被读出——禁止说出「林浅」「星野」「S1」等任何非台词文字。
- 角色语音必须严格按各角色声线档案发声（对话数据「声线：」标注），不得偏离档案音色；未标注声线的角色可自由发挥。
- **角色语音性别必须与声线档案严格一致**：prompt 中该角色的音色描述必须显式包含 male 或 female（男声=male、女声=female），禁止仅用 boy/girl 或纯中性音色词描述有声线档案的角色声线——`clear boy voice` 必须写为 `clear male voice`/`male youth voice`，保证模型按档案性别发声。
- 台词一律通过人物说话发声呈现，严禁把台词渲染为画面字幕/气泡/文字。

【negative_prompt 规则】
覆盖手部畸形、多余手指、肢体变形、面部崩坏、五官错位、模糊、低分辨率、噪点、文字、水印、签名、多余人物、构图裁切等，英文逗号分隔，15~30 个短语。"""


def _build_dialogue_block(
    segment: Segment,
    narrator_voice: str = "",
    speaker_voices: dict[str, str] | None = None,
) -> str:
    """构造分镜对白/旁白上下文（中文，供视频增强让模型原生演绎）。

    - 对白：dialogue_lines（权威源），格式「XX用中文说：「台词」」——直接台词指令。
      踩坑：Agnes 视频模型对"旁白（画外音）：台词"这类叙述式描述会输出英文纪录片腔；
      实测只有"saying '中文原文'"/「XX用中文说：「原文」」式直接指令才会按中文原句朗读。
    - 角色声线：speaker_voices 为「角色名 → 声线描述」映射（仅已定义声线档案的角色），
      注入到对应对白行——生视频时角色声音严格按声线档案执行；未定义档案的角色
      不注入，模型自由发挥（2026-08-09）。
    - 旁白：narration 追加为画外音，同样用「用中文朗读」指令。
      2026-08-09：narrator_voice 为项目旁白音色描述（narrator_profile 解析），
      注入旁白朗读指令固定声线——否则 MiniMax H3 原生语音旁白音色随机
      （实测两个视频一男一女），必须显式指定音色才能一致。
    """
    parts: list[str] = []
    voices = speaker_voices or {}
    lines = getattr(segment, "dialogue_lines", None) or []
    if lines:
        for dl in lines:
            speaker = dl.get("speaker") or "角色"
            text = (dl.get("text") or "").strip()
            if not text:
                continue
            emotion = dl.get("emotion")
            emotion_suffix = f"（{emotion}情绪）" if emotion else ""
            # 2026-08-09（M1）：声线优先按 character_id 匹配（对白 speaker 可能≠资产名），回退按名字
            voice_desc = (
                voices.get(str(dl.get("character_id")))
                or voices.get(speaker)
                or ""
            )
            voice_suffix = f"，声线：{voice_desc}" if voice_desc else ""
            # 2026-08-10：内心独白（kind=inner）——角色本人声音画外音、画面中嘴唇不动，
            # 与开口对白（dialogue）区分；音色仍是角色本人，不是叙述者
            kind = (dl.get("kind") or "dialogue").strip() or "dialogue"
            if kind == "inner":
                parts.append(
                    f"{speaker}内心独白（角色本人声音，画外音，画面中该角色嘴唇不动、"
                    f"不得对口型）用中文说：「{text}」{emotion_suffix}{voice_suffix}"
                )
            else:
                parts.append(
                    f"{speaker}用中文说：「{text}」{emotion_suffix}{voice_suffix}"
                )
    elif segment.dialogue and segment.dialogue.strip():
        # 旧数据回退：整体作为对白段落
        parts.append(f"角色用中文说：「{segment.dialogue.strip()}」")
    if segment.narration and segment.narration.strip():
        # 旁白必须是画外音：叙述者不在画面中，画面中角色不得开口念旁白
        voice_hint = f"，{narrator_voice}" if narrator_voice else ""
        parts.append(
            f"旁白（画外音{voice_hint}，叙述者不在画面中）用中文（普通话）朗读："
            f"「{segment.narration.strip()}」，画面中角色不得开口念旁白"
        )
    return "\n".join(parts) if parts else "（无对白/旁白）"


def _resolve_text_model(db: Session, model_id, scene_code: str) -> Model:
    """解析文本增强模型：优先指定 > 默认匹配 scene_code > 任意启用匹配。"""
    if model_id:
        m = db.get(Model, model_id)
        if not m or not m.is_enabled:
            raise ValueError("模型不存在或已停用")
        return m
    m = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.text,
            Model.is_default.is_(True),
            Model.is_enabled.is_(True),
            Model.scene_codes.contains([scene_code]),
        )
    )
    if m:
        return m
    # 存在默认文本模型但被停用：明确报错，禁止静默回退顶替（与 _resolve_model 一致）
    default_disabled = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.text,
            Model.is_default.is_(True),
            Model.is_enabled.is_(False),
            Model.scene_codes.contains([scene_code]),
        )
    )
    if default_disabled:
        raise ValueError(
            f"默认文本模型「{default_disabled.name}」已停用，"
            f"请先在模型管理中启用，或为「{scene_code}」重新指定默认模型"
        )
    m = db.scalar(
        select(Model).where(
            Model.model_type == ModelType.text,
            Model.is_enabled.is_(True),
            Model.scene_codes.contains([scene_code]),
        )
    )
    if not m:
        raise ValueError(f"未配置可用的「{scene_code}」文本模型，请先在模型管理启用")
    return m


def _extract_json(text: str) -> dict:
    """从 LLM 输出提取 JSON（兼容 markdown fence 与前后杂字、尾随逗号）。"""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError(f"LLM 输出不含 JSON 对象：{s[:200]!r}")
    s = s[i : j + 1]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return json.loads(s, strict=False)


# H3 官方 Ref2VA 六段式字段（h3-prompt-writing ref-en.txt）——按固定顺序出现。
# 2026-08-11：视频增强输出后校验六段是否齐全，缺失时告警（LLM 偶发漏段，
# 不阻断生成——回退逻辑由调用方负责；但需日志暴露以便监控）。
_SIX_SECTION_FIELDS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

# 六段式字段 → 中文段标题（用于「中英对照」展示/落库；LLM 输出为英文 H3，正文照抄）
_SIX_SECTION_HEADINGS_ZH: dict[str, str] = {
    "subject_definitions": "主体定义（Subject Definitions）",
    "summary": "剧情概要（Summary）",
    "retention_analysis": "一致性与保留分析（Retention Analysis）",
    "detailed_description": "详细画面描述（Detailed Description）",
    "overall_soundscape": "整体声景（Overall Soundscape）",
    "non_diegetic_music": "背景配乐（Non-diegetic Music）",
}


def to_bilingual_six_sections(prompt: str) -> str:
    """英文 H3 Ref2VA 六段式 → 中英对照文本。

    用途：写入分镜提示词（desc）落库持久化，供用户跨分镜切换查看/编辑；
    各段英文正文照抄（生成链路仍以同源英文六段式缓存执行，语义不变）。
    六段字段行未被识别时原样返回（保底，不破坏内容）。
    """
    if not prompt:
        return ""
    fields = list(_SIX_SECTION_HEADINGS_ZH.keys())
    anchors: list[tuple[int, str]] = []
    for f in fields:
        m = re.search(rf"(?im)^\s*{re.escape(f)}\s*:", prompt)
        if m:
            anchors.append((m.start(), f))
    if len(anchors) < 2:
        return prompt
    out: list[str] = []
    for i, (pos, field) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(prompt)
        body = prompt[pos:end].strip()
        # 去掉段字段行（标题已含），正文另起展示
        if ':' in body:
            body = body.split(':', 1)[1].strip()
        out.append('【' + _SIX_SECTION_HEADINGS_ZH[field] + '】\n' + body)
    return '\n\n'.join(out)


def _check_six_sections(prompt: str) -> list[str]:
    """校验 Ref2VA 六段式是否齐全，返回缺失的字段名列表（空=齐全）。"""
    missing = [f for f in _SIX_SECTION_FIELDS if f not in prompt]
    if missing:
        logger.warning(
            "[video enhance] Ref2VA 六段式缺段: %s（prompt 前 120 字: %s）",
            missing, prompt[:120],
        )
    return missing


_CJK_TRAIL_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")

def _cjk_leak_runs(prompt: str, allow_tokens: list[str] | None = None) -> list[str]:
    """检测英文六段式正文中泄漏到禁止区外的中文字符串（角色名/台词除外），返回片段列表。

    允许区：<d>...</d> 内台词原文、「...」内角色名、双引号内画面可见文字。
    allow_tokens：分镜实际绑定的角色/场景/道具名（CJK 片段），允许在正文裸用，
    避免「凌霜」这类角色名正文裸用被误报。
    单字漏词（如 的/交错/灰暗 混进英文句）也会被抓出。
    """
    allow: set[str] = set(t for t in (allow_tokens or []) if t)
    protected_parts = (
        re.findall(r"<d>.*?</d>", prompt, flags=re.S)
        + re.findall(r"「[^」]*」", prompt)
        + re.findall(r'"[^"]*"', prompt)
    )
    bare = prompt
    for part in protected_parts:
        bare = bare.replace(part, " " * len(part))
    for tok2 in allow:
        bare = bare.replace(tok2, " " * len(tok2))
    leaks: list[str] = []
    for m in _CJK_TRAIL_RE.finditer(bare):
        tok = m.group(0)
        if any(tok in p for p in protected_parts):
            continue
        if tok not in leaks:
            leaks.append(tok)
    return leaks


def _blank_slot_runs(prompt: str) -> list[str]:
    """检测六段式正文中的「空槽/占位残留」——LLM 偶发把表情/情绪写成空白槽
    （如 "is   with a trace of   —"、"of   ,"），指令即残留真空。
    只匹配行内双空格（[ \t]{2,}），段落空行（\n\n）属正常结构、不算空槽。"""
    return list(dict.fromkeys(re.findall(r"\S[ \t]{2,}\S", prompt))) if prompt else []


def _sanitize_blank_slots(prompt: str) -> str:
    """把空槽的多余空白归一为单空格（仅清洗，不改写语义）。"""
    return re.sub(r"[ \t]{2,}", " ", prompt) if prompt else prompt


def _sanitize_cjk_runs(prompt: str, allow_tokens: list[str] | None = None) -> str:
    """剥离英文六段式正文中泄漏的中文字符串（角色名/台词除外），返回清洗后的文本。

    仅在最后一次重试仍泄漏时使用：剥除泄漏的文字段，保住六段式结构，
    避免整个六段式被丢弃回退为原始中文描述。
    """
    allow: set[str] = set(t for t in (allow_tokens or []) if t)
    protected_parts = (
        re.findall(r"<d>.*?</d>", prompt, flags=re.S)
        + re.findall(r"「[^」]*」", prompt)
        + re.findall(r'"[^"]*"', prompt)
    )
    bare = prompt
    for part in protected_parts:
        bare = bare.replace(part, "\x00" * len(part))
    cleaned = list(bare)
    for m in _CJK_TRAIL_RE.finditer(bare):
        tok = m.group(0)
        if any(tok in p for p in protected_parts) or tok in allow:
            continue
        for idx in range(m.start(), m.end()):
            cleaned[idx] = " "
    cleaned = "".join(cleaned)
    # 还原保护区
    for part in protected_parts:
        cleaned = cleaned.replace("\x00" * len(part), part, 1)
    return cleaned

def _segment_cjk_allow_tokens(db: Session, segment: Segment) -> list[str]:
    """分镜绑定资产名中的 CJK 片段（角色/场景/道具名），供 CJK 泄漏检测放行。"""
    import re as _re2

    out: list[str] = []
    names: list[str] = []
    for cid in (segment.character_ids or []):
        a = db.get(Asset, uuid.UUID(str(cid)))
        if a and a.name:
            names.append(a.name)
    if segment.scene_id:
        a = db.get(Asset, uuid.UUID(str(segment.scene_id)))
        if a and a.name:
            names.append(a.name)
    for pid in (segment.prop_ids or []):
        a = db.get(Asset, uuid.UUID(str(pid)))
        if a and a.name:
            names.append(a.name)
    for n in names:
        for tok in _re2.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]+", n or ""):
            if tok:
                out.append(tok)
    return out


def _asset_brief(asset: Asset) -> str:
    """资产上下文描述：优先扩写描述，回退原始描述。"""
    desc = asset.expanded_description or asset.description or asset.name
    return f"[{asset.name}] {desc}"


def _build_assets_block(db: Session, segment: Segment) -> str:
    """收集分镜相关的角色/场景/道具描述（英文扩写），拼成上下文块。"""
    lines: list[str] = []

    def _get(asset_id) -> Asset | None:
        try:
            return db.get(Asset, uuid.UUID(str(asset_id)))
        except (ValueError, TypeError):
            return None

    for cid in segment.character_ids:
        a = _get(cid)
        if a:
            lines.append(f"- 角色：{_asset_brief(a)}")
    if segment.scene_id:
        a = _get(segment.scene_id)
        if a:
            lines.append(f"- 场景：{_asset_brief(a)}")
    for pid in segment.prop_ids:
        a = _get(pid)
        if a:
            lines.append(f"- 道具：{_asset_brief(a)}")
    return "\n".join(lines) if lines else "- （无资产上下文）"


# 中文画面描述中出现这些词语即视为「本镜有人在场」——辅助判定，弥补 character_ids 未绑定时
#（如纯旁白提到路/路人、或在册资产未绑定）的漏判，避免把「其实有人」的镜头误判为空镜
# 而强令「禁止出现人物」。2026-08-31 修正 _scope_flags_block 纯靠 character_ids 的误伤。
_PERSON_MARKERS = (
    "主角", "人", "女", "男", "男孩", "女孩", "女子", "男子", "少女", "少年", "老者",
    "她", "他", "他们", "她们", "大家", "众人", "两人", "三人", "人群", "路人",
    "观众", "客人们", "顾客", "服务生", "保安", "警察", "医生", "护士", "老师",
    "孩子", "宝宝", "母亲", "父亲", "兄弟", "姐妹", "背影", "侧脸", "仰头", "低头",
)
# 群像/多人标记：命中即视为「多人同框」镜头，用于 (a) 注入负面词时**不**追加
# multiple people（避免群像被抑制）、(b) 正文允许合理多人在场。
_GROUP_MARKERS = (
    "两人", "三人", "多人", "众人", "大家", "他们", "她们", "一群", "人群", "群像",
    "并肩", "对峙", "面对面", "相对而立", "站成", "围坐", "围观", "同框", "并排",
    "互相", "彼此", "双方", "两", "三", "们",
)


def _shot_has_person(segment) -> bool:
    """本镜是否有人在场：人物资产绑定 / 对白有说话人 / 描述出现人称标记 任一即 True。"""
    if getattr(segment, "character_ids", None):
        return True
    dl = getattr(segment, "dialogue_lines", None) or []
    if any((x.get("speaker") or "").strip() for x in dl if (x.get("text") or "").strip()):
        return True
    desc = (segment.description or "").strip()
    return any(m in desc for m in _PERSON_MARKERS) if desc else False


def _shot_is_group(segment) -> bool:
    """本镜是否属群像/多人同框（负面词不应追加 multiple people / 正文允许多人）。"""
    char_ids = getattr(segment, "character_ids", None) or []
    if len(char_ids) >= 2:
        return True
    dl = getattr(segment, "dialogue_lines", None) or []
    speakers = {x.get("speaker") or "" for x in dl if (x.get("text") or "").strip()}
    if len(speakers) >= 2:
        return True
    desc = (segment.description or "").strip()
    return any(m in desc for m in _GROUP_MARKERS) if desc else False


def _scope_flags_block(segment) -> str:
    """按本镜实际特征生成「硬约束适用性」提示，供 LLM 只执行相关约束（2026-08-31）。

    此前视频模板一次性注入全部硬约束，纯风景/无人物/无对白/无道具的镜头也背着
    「角色数量严格一致」「妆容外观稳定」「屏幕文字可读」等无关约束——浪费 token 且
    可能在下令空气里强行迁就人物约束，反而挤出多余人物。

    规则：
    - 无人物/无台词 → 跳过人物在场/口型/妆容/声线类约束，画面严禁出现人物；
    - 无道具 → 跳过「道具交接一致性」；
    - 无对白/无旁白 → 跳过「<d> 朗读」「中文语音」类约束，六段式不写 (Sx)/<d>；
    - 无动作（无位移/入画/开门等动作词）→ 运镜兜底不强制执行。
    返回行为指令，随模板注入；恒为非空，保证模型明确知道该跳过哪些。
    """
    desc = (segment.description or "").strip()
    char_ids = getattr(segment, "character_ids", None) or []
    has_char = _shot_has_person(segment)
    is_group = _shot_is_group(segment)
    has_prop = bool(getattr(segment, "prop_ids", None))
    dl = getattr(segment, "dialogue_lines", None) or []
    has_dialogue = bool(dl and any((x.get("text") or "").strip() for x in dl))
    has_narration = bool((segment.narration or "").strip())
    camera = (segment.camera or "").strip()
    # 运镜兜底判定：描述含位移/入画/开门等动作词，或显式运镜为推/拉/摇/移/跟（非固定）。
    # 修正：显式「固定」是作者意图（真·静止），此时即使描述含动作词也不强制推近，
    # 避免「有动作却整镜静止」与运镜兜底意图相悖。
    has_motion = bool(
        (any(v in desc for v in _VIDEO_MOTION_VERBS) if desc else False)
        or (camera and camera != "固定")
    )
    lines = ["【本镜特征（硬约束按此应用，与本镜无关的约束一律跳过）】"]
    if not has_char:
        lines.append("- 本镜无人物在场：画面必须无人物/无人脸，跳过人物数量、妆容、视线、口型、声线类约束（更不该添加任何人物或图鉴式排列）。")
    elif is_group:
        lines.append("- 本镜为多人/群像在场：允许多人同框（对峙/交谈/并肩等），且须保持在场角色数量与分镜描述一致；人物相关约束全部生效。")
    elif char_ids:
        lines.append(f"- 本镜有 {len(char_ids)} 位角色在场（{list(char_ids)}），人物相关约束全部生效。")
    else:
        lines.append("- 本镜人物在场（来自对白/画面主体，未见绑定资产）：人物相关约束全部生效，人物数量与分镜描述一致。")
    if not has_prop:
        lines.append("- 本镜无道具交接：跳过「道具交接一致性」约束。")
    if not (has_dialogue or has_narration):
        lines.append("- 本镜无对白/无旁白：六段式不写 (Sx) 说话人与 <d> 台词；跳过中文语音/朗读/声线类约束。")
    if not has_motion:
        lines.append("- 本镜无主体位移/入场/开门等动作、且未显式指定运镜：运镜兜底不强制执行，可按静态或缓慢推近自定。")
    return "\n".join(lines)


def _enhance_with_llm(
    db: Session,
    segment: Segment,
    style_prompt: str | None,
    target: str,
    model_id=None,
    max_retries: int = 4,
    bilingual: bool = False,
    ref_labels: list[str] | None = None,
    narrator_profile: dict | None = None,
    speaker_voices: dict[str, str] | None = None,
    lang: str = "en",
    project=None,
) -> tuple[str, str]:
    """调 LLM 生成 (prompt, negative_prompt)。失败抛异常，由调用方兜底。

    project：项目（用于按有效风格裁剪视频 AI 味负面词，见 _video_ai_negatives_for_style）。
    bilingual=True：输出中英双语 prompt（中文段 + English 完整翻译段），
    供英文原生模型（Flux.2 Klein 等）使用，兼顾中文语义精度与英文生图质量。
    ref_labels：参考图语义标签（角色/场景/道具名），注入提示词让 LLM 在 prompt
    中显式用 Image N 指代并强化一致性（官方 Multi-Reference 最佳实践）。
    narrator_profile：项目旁白声线（Project.narrator_profile），视频增强时
    解析为旁白音色描述注入朗读指令，固定旁白声线（2026-08-09）。
    speaker_voices：角色声线映射（角色名 → 音色描述，仅已定义声线档案的角色），
    注入对白行——生视频时角色声音严格按声线档案执行，无档案角色自由发挥
    （2026-08-09）。
    """
    is_video = target == "video"
    target_label = "视频" if is_video else "图片"
    # 对白/旁白上下文：仅视频增强注入（图片增强不需要，避免生图 prompt 混入台词）
    from app.services.character_voice_service import narrator_voice_description

    narrator_voice = narrator_voice_description(narrator_profile) if is_video else ""
    # 角色声线映射：已定义声线档案的角色 → 声音严格按档案执行；无档案角色不注入（模型自由发挥）
    speaker_voices = dict(speaker_voices or {}) if is_video else {}
    dialogue_block = _build_dialogue_block(
        segment,
        narrator_voice=narrator_voice,
        speaker_voices=speaker_voices,
    ) if is_video else "（不注入）"
    has_dialogue = is_video and "（无对白/旁白）" not in dialogue_block and "（不注入）" not in dialogue_block
    # 对话演绎规则：模型原生语音——角色开口说对白（中文原句）、旁白画外音、情绪到位。
    # 关键区分：引号内为台词（语音发声），严禁台词文字化渲染为字幕/气泡；
    # 语言硬约束：所有角色语音必须且只能用简体中文说出（Agnes 视频模型对白默认会中英自由发挥，
    # 须在提示词中强制中文，禁止中英混杂）。
    # 环境中的自然文字（招牌/路标/报纸等）属于画面真实性的一部分，允许保留。
    narrator_voice_rule = (
        f"；旁白音色固定为「{narrator_voice}」，全片旁白保持同一音色、不得变化"
        if narrator_voice
        else ""
    )
    # 角色声线执行规则：已定义声线档案的角色声音严格按档案发声；
    # 未标注声线的角色不约束（模型自由发挥）。
    speaker_voice_rule = ""
    if speaker_voices:
        voice_list = "、".join(
            f"{name}必须用「{desc}」声线发声" for name, desc in speaker_voices.items()
        )
        speaker_voice_rule = (
            f"；角色语音必须严格按各角色声线档案发声——{voice_list}，"
            "不得偏离档案音色；未标注声线的角色可自由发挥"
        )
    dialogue_rule = (
        (
            "；本镜存在对白/旁白，必须让角色在画面中开口说出对白——引号「」内的是台词语音，"
            "保留中文原句、语气按标注情绪；旁白是画面外的叙述声音，必须由不在画面中的叙述者"
            "用中文朗读，画面中所有角色都不得开口念旁白、不得把旁白当作角色台词朗读；"
            f"{narrator_voice_rule}"
            f"{speaker_voice_rule}"
            "所有角色语音必须且只能用简体中文（普通话，zh-CN）说出，逐字说出引号内的中文原句，"
            "绝对禁止翻译成英语或任何外语、禁止夹杂任何英文单词、禁止中英混杂——"
            "任何英文发音或英文单词都是严重错误；"
            "台词一律通过人物说话发声呈现，严禁把台词渲染为画面字幕/气泡/文字；"
            "画面中的环境自然文字（招牌/路标/报纸等）可正常保留"
        )
        if has_dialogue
        else ""
    )
    # 主体规则：视频要求角色外貌与资产描述严格一致（跨分镜一致性锚点）+ 表情眼神；
    # 图片保持原「引用外观细节」。
    subject_rule = (
        "点名出现的角色与道具（保留中文名），严格引用其资产描述中的外貌细节（发型、瞳色、"
        "服饰、体型），外貌必须与描述完全一致、不得改动；同时写清表情与眼神（如：眼神坚定、"
        "眉梢微挑、嘴唇紧抿），避免呆滞空洞；强调人物面部结构始终稳定一致、五官清晰、"
        "比例协调，说话与表情变化时面部自然流畅、不扭曲不变形；"
        "**皮肤真实感（最高优先级，真人/电影风格）**：人脸必须是真实真人皮肤——保留自然毛孔、"
        "细纹、微瑕疵与肤色过渡，柔和的漫射光、适中的对比度，严禁磨皮/过度平滑/塑料/蜡像脸；"
        "**严禁过曝高光**：局部不得出现死白高光/白斑/纯白皮肤，五官与面颊要有温和的明暗过渡与"
        "可感知的阴影，脸部高光为柔和的自然光泽而非刺眼镜面反光"
        if is_video
        else "点名出现的角色与道具（保留中文名），引用其描述中的外观细节，用限定词约束外观（颜色/材质/数量/位置）"
    )
    # 动作/质感规则：视频强调动作时序与物理重量感 + 材质质感（对抗塑料平滑感）；
    # 图片保持原「动作姿态表情」。
    action_rule = (
        "写清人物动作的时间顺序（先…然后…）与力度/重量感（如：猛地推开门、踉跄后退、"
        "攥紧拳头），身体语言与表情情绪一致；补充材质质感——皮肤自然纹理与毛孔、织物褶皱、"
        "金属反光、水花飞溅，避免塑料感/过度平滑/磨皮质感；皮肤用哑光自然肤质，局部不打"
        "过亮的高光、不出现镜面反光与死白过曝"
        if is_video
        else "根据画面描述写清人物动作、姿态、表情"
    )
    target_rule = (
        "运镜翻译（固定=static camera、推=push in、拉=pull back、摇=pan、移=dolly tracking shot、跟=follow shot），"
        "写清镜头运动方向与节奏；若给出运镜参数（速度/角度/幅度），按参数写清镜头运动速度与角度"
        "（如：缓慢推近、低角度仰拍、幅度微弱）；人物动作写动态时序（先…然后…）；补充环境微动态"
        "（雾气流动/灯光闪烁/水面反光/衣摆飘动/树叶轻摆等）；保持 24fps 电影感；若画面存在声音场景"
        "（街道/雨/风/人群等），"
        f"追加一句环境音描述以引导模型生成原生音频{dialogue_rule}"
        if is_video
        else "静态电影级构图，写清画面各元素的空间关系与景深层次（前景/中景/背景）"
    )
    # 电影级成片质感：关键帧偏静态画质（浅景深/胶片颗粒），视频偏动态连贯（平滑运动/原生环境音）
    quality_tail = (
        "结尾追加电影级质量词：highly detailed, realistic skin texture and pores, "
        "soft natural diffused lighting, subtle film grain, smooth 24fps motion, "
        "natural realistic photography, 8k"
        if is_video
        else "结尾追加电影级质量词：highly detailed, realistic skin texture and pores, "
        "soft natural diffused lighting, subtle film grain, natural realistic photography, 8k"
    )
    # 语言规则（2026-08-12 更新）：生图/生视频 prompt 统一默认英文——
    # 主流生图/生视频模型（FLUX/Stable Diffusion/Seedream/MiniMax H3）训练数据以英文为主，
    # 英文 prompt 语义最精确（FLUX 官方文档明确"English prompts tend to produce the most precise results"）。
    # 画面中的自然文字（招牌/路标）与角色语音对白保留中文原文（引号 / <d>[Chinese]</d> 标签承载）。
    # bilingual=True（Flux.2 Klein 等英文原生多图编辑模型）：英文为主 + 中文辅助段做语义校准。
    # 官方结构模板：
    #   [SUBJECT], [LOCATION], [STYLE], [CAMERA SETTINGS], [LIGHTING], [COLORS], [EFFECT]
    if bilingual:
        language_rule = (
            "提示词输出结构为：\n"
            "【English】英文自然语言完整描述（**主提示词**，FLUX 官方最佳实践："
            "按「主体→位置→风格→相机设置→光照→色彩→特效→补充元素」顺序，"
            "用自然语言流畅描述整幅画面，像给画家描述照片一样，禁止碎片化关键词堆砌；"
            "风格标签（Art style）、镜头语言（medium shot/close-up）、质量词（8k uhd）保留英文原生表达）\n"
            "【中文】中文辅助描述（简体中文，与英文段语义一致，供语义校准；角色语音用中文原句）\n"
            "两段内容一一对应、语义完全一致。\n"
            "角色语音一律为简体中文（普通话，zh-CN）：所有说话内容必须且只能用中文，"
            "绝对禁止翻译成英语或任何外语、禁止夹杂任何英文单词——英文段中角色台词保留中文原文"
            "并加引号（FLUX 官方：引号内文字渲染为画面文字）；\n"
            "如画面中出现招牌/路标/报纸等自然文字，须为简体中文且拼写正确、加引号，"
            "英文段保留中文原文（如 a signboard with the Chinese characters '欢迎光临'）"
        )
        # 结构化 JSON 控制块（FLUX.2 官方结构化 Prompting 特性）：prompt 末尾附加精确控制段
        structured_rule = (
            "\n10. 在 prompt 末尾附一段「结构化 JSON 控制块」（FLUX.2 官方结构化 Prompting），"
            "覆盖关键属性，格式：\n"
            '{"subject": "主体描述（与上文一致）", "background": "背景/位置", '
            '"lighting": "光照描述", "style": "Art style 原样", '
            '"camera_angle": "镜头角度（如 eye level view）", '
            '"composition": "构图（如 centered, balanced）"}\n'
            "JSON 键名固定为 subject/background/lighting/style/camera_angle/composition，"
            "值为对应属性的英文短描述，与【English】段一致；如无对应信息可省略该键。"
        )
    elif is_video:
        # 2026-08-12：生视频 prompt 默认英文（H3/Ref2VA 模板本身为英文六段式，
        # 英文描述更精确）；角色语音对白保留中文原句（<d>[Chinese]</d> 标签承载）。
        language_rule = (
            "提示词使用英文编写（detailed_description 等画面描述全用英文，风格/景别/运镜/"
            "质量词保留英文原生表达）。角色语音一律为简体中文（普通话，zh-CN）：所有说话内容"
            "必须且只能用中文原句（<d>[Chinese] 中文原句</d>），绝对禁止翻译成英语或任何外语、"
            "禁止夹杂任何英文单词、禁止中英混杂——任何英文发音或英文单词都是严重错误；"
            "如画面中出现招牌/路标等自然文字，须为简体中文且拼写正确、加引号"
        )
        structured_rule = ""
    else:
        # 2026-08-12：生图 prompt 默认英文（社区/厂商共识：英文 prompt 对主流生图模型"
        # （FLUX/Stable Diffusion/Seedream 等）语义最精确；自然文字类画面元素保留中文原文加引号）
        language_rule = (
            "提示词使用英文自然语言完整描述整幅画面（主体/外观/动作/环境/光线/氛围/景别全用英文；"
            "风格关键词与质量词保留英文原生表达），按「主体→位置→风格→光照→色彩→构图」顺序"
            "流畅书写，禁止碎片化关键词堆砌。如画面中出现招牌/路标/报纸等自然文字，"
            "须为简体中文且拼写正确、加引号（如 a signboard with the Chinese characters '欢迎光临'）"
        )
        structured_rule = ""
    assets_block = _build_assets_block(db, segment)
    style_line = style_prompt or "（无指定风格）"
    # 正文语言规则：lang=zh 时六段式正文用简体中文；字段名/标签/固定标记保持英文
    #（<Subject N>、<d>[Chinese] 中文原句</d>、[Shot N]、(S1)、Art style:、At MM:SS.mmm 等），
    # 保证 to_bilingual_six_sections / _check_six_sections 等解析不受正文语言影响。
    if str(lang).strip().lower() == "zh" and is_video:
        output_lang_rule = (
            "六段式正文一律用简体中文书写；字段名行保持英文小写蛇形"
            "（subject_definitions: / summary: / retention_analysis: / detailed_description: / "
            "overall_soundscape: / non_diegetic_music:），标签与固定标记保持英文"
            "（<Subject N> / <Picture N> / <d>[Chinese] 中文原句</d> / [Shot N] / (S1) / "
            "Art style: / At MM:SS.mmm 等）；中文正文须与英文版语义等价、信息密度一致、"
            "细节完整；<d> 内台词/旁白必须逐字保留中文原句；"
            "画面可见自然文字保留简体中文。"
        )
    elif str(lang).strip().lower() == "zh":
        output_lang_rule = (
            "整段提示词用简体中文书写；固定标记（Art style / 镜头专业词汇等）可按需保留英文。"
        )
    else:
        output_lang_rule = (
            "正文使用英文书写（画面描述/风格/景别/运镜/质量词保留英文原生表达）。"
        )
    # 参考图指代上下文（bilingual 多图编辑 / R2V 视频链路）：让 LLM 在 prompt 中显式用
    # Image N 指代参考图并强化一致性指令（官方 Multi-Reference 最佳实践："Use Image 2 as
    # the location, keep the face from Image 1"）。无参考图（文生图）时为空。
    # 2026-08-10：R2V 视频链路也注入——六格多视角示意图必须声明「仅供空间理解、
    # 网格不入画」，模型才知道该图职责，避免渲染成画面内容。
    ref_block = ""
    if ref_labels:
        list_block = "\n".join(f"- Image {i + 1}: {label}" for i, label in enumerate(ref_labels))
        if is_video:
            ref_block = (
                "\n\n【参考图清单（按传入顺序编号，Image N 与系统传入的第 N 张参考图严格对应）】\n"
                + list_block
                + (
                    "\n编写 <Subject N> 定义时，必须按上表 Image 编号与语义一一对应声明特征来源"
                    "（如「<Subject 1> is the scene from Image 1 …」）；其中多视角示意图类参考图"
                    "仅用于理解场景空间结构与各角度外观，网格/分格线/拼图本身不是画面内容，"
                    "绝对不得渲染进视频画面。"
                )
            )
        elif bilingual:
            ref_block = (
                "\n\n【参考图指代（仅编辑链路）】系统会向生图模型传入以下参考图：\n"
                + list_block
                + (
                    "\n编写 prompt 时：涉及该参考图元素处，须显式写 Image N 指代，"
                    "并声明保持一致性——如「the character from Image 1」（面部/发型/服装/姿态"
                    "必须与 Image 1 完全一致，不得改动）、「the location from Image 2」（场景"
                    "构图/环境元素须采用 Image 2）；只允许在 Image N 之外的画面区域自由构图。"
                    "编号必须与上表严格一一对应：Image N 就是第 N 张参考图，引用某元素时"
                    "必须使用其所在行对应的唯一编号，禁止把所有元素都写成 Image 1、禁止"
                    "自行换序或合并编号；每张参考图的语义以上表标注为准。"
                )
            )
        else:
            ref_block = "\n\n【参考图清单】系统会向生图模型传入以下参考图：\n" + list_block

    # 视频：H3 官方 Ref2VA 六段式模板（2026-08-09 整合 h3-prompt-writing 技能 ref-en.txt 规范）；
    # 图片：原 _ENHANCE_TMPL（含 bilingual / 参考图 Image N 指代等编辑链路特性）。
    if is_video:
        continuity_block = _continuity_block(db, segment)
        prompt_text = _ENHANCE_VIDEO_TMPL.format(
            description=segment.description or "",
            shot_type=segment.shot_type or "",
            # 2026-08-28 运镜兜底：无显式运镜且有动作时默认「缓慢推近」，避免整镜静止
            camera=_video_camera_hint(segment),
            emotion=segment.emotion or "平静",
            dialogue_block=dialogue_block,
            beats_block=format_beats_block(getattr(segment, "shot_beats", None), segment.duration),
            assets_block=assets_block,
            ref_block=ref_block,
            # 2026-08-16：分镜时长由 LLM 按内容自动配置（5~15s），增强 prompt 注入真实时长
            duration_seconds=str(segment.duration or 5.0),
            style=style_line,
            narrator_voice=narrator_voice or "中性稳重男声",
            output_lang_rule=output_lang_rule,
            continuity_block=continuity_block,
            scope_flags=_scope_flags_block(segment),
        )
    else:
        prompt_text = _ENHANCE_TMPL.format(
            target_label=target_label,
            description=segment.description or "",
            shot_type=_SHOT_TYPE_EN.get(segment.shot_type or "", segment.shot_type or "medium shot"),
            camera=_CAMERA_EN.get(segment.camera or "", segment.camera or "static camera"),
            emotion=segment.emotion or "平静",
            dialogue_block=dialogue_block,
            assets_block=assets_block + ref_block,
            subject_rule=subject_rule,
            action_rule=action_rule,
            target_rule=target_rule,
            quality_tail=quality_tail,
            language_rule=language_rule,
            style=style_line,
            structured_rule=structured_rule,
        )

    model = _resolve_text_model(db, model_id, "script")
    provider = ProviderRegistry.for_model(model)
    system_text = "你是专业的 AI 提示词工程师，只输出符合要求的 JSON。"
    if str(lang).strip().lower() == "zh":
        system_text += (
            " 六段式正文必须用简体中文书写——subject_definitions/summary/retention_analysis/"
            "detailed_description/overall_soundscape/non_diegetic_music 六段除字段名与固定标签"
            "（<Subject N>/<d>[Chinese] 中文原句</d>/[Shot N]/(S1)/Art style:/At MM:SS.mmm）外"
            "一律写简体中文，这是最高优先级要求，不得用英文书写正文。"
        )
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": prompt_text},
    ]
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat(messages)
            content = resp["choices"][0]["message"]["content"]
            data = _extract_json(content)
            prompt = (data.get("prompt") or "").strip()
            negative = (data.get("negative_prompt") or "").strip()
            # lang=zh：优先用 LLM 的 prompt_zh（逐字段对齐的中文六段式）；
            # 未返回时回退英文 prompt（LLM 偶发漏字段，兜底不阻断）。
            if str(lang).strip().lower() == "zh":
                prompt_zh = (data.get("prompt_zh") or "").strip()
                prompt = prompt_zh or prompt
            if not prompt:
                raise ValueError("LLM 未返回 prompt")
            # 2026-08-11：视频链路校验 Ref2VA 六段式齐全性。
            # 2026-08-28 升级为硬校验：缺失内容段（detailed_description / overall_soundscape /
            # non_diegetic_music）或出现空槽占位一律抛错重试——残缺 prompt 出片 = 无镜头指令，
            # 实测 seg07/10 因缺 detailed_description 出「人困在电梯里不动」的废片。
            #   缺段仅告警不阻断 → 改为缺失即重试，重试耗尽由调用方回退原始描述（不存残缺稿）。
            if is_video:
                _missing = _check_six_sections(prompt)
                _hard_missing = [f for f in ("detailed_description",) if f + ":" not in prompt]
                if _hard_missing:
                    if attempt >= max_retries:
                        raise ValueError("六段式缺详细分镜正文 missing detailed_description，已重试耗尽")
                    logger.warning("[video enhance] 六段式缺 detailed_description：%s，重试", _missing)
                    raise ValueError(f"六段式缺 detailed_description（{_missing}），重试")
                # 2026-08-28：空槽占位检测（"is   with a trace of   —"），命中重试；
                # 重试耗尽仅做空白归一（不清语义），避免空槽指令真空落库。
                _blanks = _blank_slot_runs(prompt)
                if _blanks:
                    if attempt >= max_retries:
                        prompt = _sanitize_blank_slots(prompt)
                        logger.warning("[video enhance] 六段式空槽 %s ≥重试上限，空白归一后继续", _blanks[:5])
                    else:
                        logger.warning("[video enhance] 六段式空槽占位 %s，重试", _blanks[:5])
                        raise ValueError(f"六段式空槽占位: {_blanks[:5]}")
                # 2026-08-27：六段式英文正文中文泄漏检测（角色名/台词除外），
                # 命中即抛错重试，杜绝「交错/灰暗」式中英混排稀释指令。
                if str(lang).strip().lower() != "zh" and not bilingual:
                    _allow = _segment_cjk_allow_tokens(db, segment)
                    _leaks = _cjk_leak_runs(prompt, _allow)
                    if _leaks:
                        if attempt >= max_retries:
                            sanitized = _sanitize_cjk_runs(prompt, _allow)
                            if "subject_definitions:" in sanitized and "detailed_description:" in sanitized:
                                logger.warning(
                                    "[video enhance] 六段式中文泄漏 %s ≥重试上限，剥除中文段保留六段式（len %d -> %d）",
                                    _leaks, len(prompt), len(sanitized),
                                )
                                sanitized = sanitized.strip()
                                neg = negative or _FALLBACK_NEGATIVE
                                if is_video:
                                    neg = f"{neg}, {_video_ai_negatives_for_style(db, project)}".strip(", ")
                                if _shot_has_person(segment) and not _shot_is_group(segment):
                                    neg = f"{neg}, {_SINGLE_SUBJECT_NEGATIVE}".strip(", ")
                                return sanitized, neg
                        logger.warning(
                            "[video enhance] 六段式英文正文含中文泄漏 %s，重试", _leaks
                        )
                        raise ValueError(f"六段式英文正文含中文泄漏: {_leaks}")
            neg = negative or _FALLBACK_NEGATIVE
            # 视频追加 AI 化负面词（塑料/蜡像皮肤、CGI/动画感、僵硬表情运动等）；
            # 2026-08-31：按项目有效风格裁剪——非写实（动漫/3D/插画）剔除风格冲突词
            #（cel shading / cartoon style / 3d animation / ai slop 等），避免与目标风格打架。
            if is_video:
                neg = f"{neg}, {_video_ai_negatives_for_style(db, project)}".strip(", ")
            # 2026-08-31：multiple people 仅单主体镜追加，群像/多人同框不追加（见 _assemble_final_negative 注释）。
            if _shot_has_person(segment) and not _shot_is_group(segment):
                neg = f"{neg}, {_SINGLE_SUBJECT_NEGATIVE}".strip(", ")
            return prompt, neg
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
    raise last_err or RuntimeError("提示词增强失败")


def _assets_fingerprint(db: Session, segment: Segment) -> str:
    """分镜引用资产的内容指纹（角色/场景/道具的描述与扩写）。

    2026-08-09 修复：资产描述（description/expanded_description）变更后，assets_block
    注入提示词的上下文会变，但旧缓存（enhanced_target 只含音色/声线/ref 指纹）仍命中，
    导致重新生成关键帧/视频时沿用旧资产外貌描述。将引用资产的描述 hash 进缓存键，
    资产内容变化后自动失效。封面/四视图 URL 不纳入（仅外观特征来源、不入提示词文本）。
    """
    import hashlib

    briefs: list[str] = []

    def _get(aid):
        try:
            return db.get(Asset, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            return None

    for cid in segment.character_ids:
        a = _get(cid)
        if a:
            briefs.append(f"c:{a.id}:{a.name}:{a.description}:{a.expanded_description}")
    if segment.scene_id:
        a = _get(segment.scene_id)
        if a:
            briefs.append(f"s:{a.id}:{a.name}:{a.description}:{a.expanded_description}")
    for pid in segment.prop_ids:
        a = _get(pid)
        if a:
            briefs.append(f"p:{a.id}:{a.name}:{a.description}:{a.expanded_description}")
    if not briefs:
        return ""
    return hashlib.md5("|".join(briefs).encode("utf-8")).hexdigest()[:8]


def ensure_enhanced_prompt(
    db: Session,
    segment: Segment,
    project: Project | None,
    *,
    target: str = "image",
    model_id=None,
    force: bool = False,
    bilingual: bool = False,
    ref_labels: list[str] | None = None,
    lang: str = "en",
) -> tuple[str, str]:
    """获取分镜的增强 prompt 与负面词（带缓存）。

    target: "image"（生图）或 "video"（生视频），决定 LLM 提示词侧重。
    bilingual: 英文原生模型（Flux.2 Klein 等）输出中英双语 prompt；
        缓存键追加 _bilingual 后缀，与中文版缓存互不覆盖。
    ref_labels: 参考图语义标签（角色/场景/道具名），注入提示词让 LLM 用
        Image N 指代并强化参考一致性（bilingual 多图编辑链路）。
    缓存键是分镜级：首次生成后复用；force=True 强制重新生成。
    LLM 失败时不阻断：回退「原始描述 + 内置负面词」并告警。
    """
    cache_target = f"{target}_bilingual" if bilingual else target
    # 2026-08-31：模板版本纳入缓存键——修改增强模板（含任何规则/硬约束）后旧缓存自动失效，
    # 否则改了模板仍命中旧 enhanced_target，用户沿用旧规则出片（须手动 force）。
    cache_target = f"{cache_target}_v{_TEMPLATE_VERSION}"
    # 正文语言分流：zh 结构化缓存与 en 生成缓存各自独立，互不串用
    #（结构化用中文六段式保存/展示，生成仍按 en 缓存出片）
    lang_key = str(lang or "en").strip().lower()
    if lang_key in ("zh", "en") and not bilingual:
        cache_target = f"{cache_target}_l{lang_key}"
    # 2026-08-08：缓存键必须纳入 ref_labels 指纹。参考图集合（角色/场景/道具/上一镜）
    # 变化时，prompt 里的 Image N 指代编号与语义都要严格对应，复用旧缓存会导致
    # 指代缺失或错位 → 模型不知道每张参考图是什么 → 画面不按所选资产执行
    # （实测 seg2 复用无指代缓存，输出形象与资产参考图不一致）。
    # 2026-08-09：缓存键追加旁白音色指纹。narrator_profile（音色）变化时旧缓存
    # （无音色/旧音色 prompt）自动失效——否则改了旁白声线后重新生成视频仍复用旧
    # 无音色 prompt，音色固定不生效。
    import hashlib

    narrator_profile = None
    speaker_voices: dict[str, str] = {}
    if target == "video" and project is not None:
        from app.services.character_voice_service import (
            build_speaker_voice_map,
            ensure_narrator_profile,
        )

        # 2026-08-10：旁白音色由 LLM 按剧情生成（未配置时懒生成一次落库），
        # 避免默认「中性稳重男声」与男主同声。
        # 2026-08-31：旁白指纹仅在「本镜确有旁白」时纳入——旁白音色是项目级字段，
        # 若无条件拼进每个镜头，改一次项目旁白声线会让全项目所有镜头缓存失效重算
        #（即便很多镜头根本没有旁白）。无旁白的镜头不注入旁白音色约束，无需纳入指纹。
        if (segment.narration or "").strip():
            narrator_profile = ensure_narrator_profile(db, project)
            narrator_fp = hashlib.md5(
                json.dumps(narrator_profile, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:8]
            cache_target = f"{cache_target}_n{narrator_fp}"
        # 角色声线指纹：声线档案变化（新增/修改/清空）时旧缓存自动失效，
        # 否则改了声线后重新生成视频仍复用旧无声线 prompt，声线约束不生效
        speaker_voices = build_speaker_voice_map(db, segment.character_ids)
        if speaker_voices:
            voice_fp = hashlib.md5(
                json.dumps(speaker_voices, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:8]
            cache_target = f"{cache_target}_v{voice_fp}"
    if ref_labels:
        ref_fp = "_r" + hashlib.md5("|".join(ref_labels).encode("utf-8")).hexdigest()[:8]
        cache_target = f"{cache_target}{ref_fp}"
    # 2026-08-09 修复：资产描述/扩写变更后自动失效（见 _assets_fingerprint）。
    # 放在缓存命中判断之前，保证命中键与生成时的上下文完全一致。
    assets_fp = _assets_fingerprint(db, segment)
    if assets_fp:
        cache_target = f"{cache_target}_a{assets_fp}"
    # 2026-08-28：节拍指纹。shot_beats（分镜内多镜头运镜）变化时旧增强缓存自动失效，
    # 否则改了节拍后重新生成视频仍复用旧无节拍 prompt（segment_service.update 会清缓存，
    # 此处双保险覆盖直接改库/agent 改写等路径）。
    if getattr(segment, "shot_beats", None):
        beats_fp = "b" + beats_fingerprint(segment.shot_beats)
        cache_target = f"{cache_target}_{beats_fp}"
    if not force and segment.enhanced_prompt and segment.enhanced_target == cache_target:
        return segment.enhanced_prompt, segment.enhanced_negative_prompt or _FALLBACK_NEGATIVE

    from app.services.style_service import get_effective_style_prompt
    style_prompt = get_effective_style_prompt(db, project)

    try:
        prompt, negative = _enhance_with_llm(
            db, segment, style_prompt, target, model_id=model_id, bilingual=bilingual,
            ref_labels=ref_labels, narrator_profile=narrator_profile,
            speaker_voices=speaker_voices, lang=lang, project=project,
        )
        segment.enhanced_prompt = prompt
        segment.enhanced_negative_prompt = negative or _FALLBACK_NEGATIVE
        segment.enhanced_target = cache_target
        db.commit()
        return prompt, segment.enhanced_negative_prompt
    except Exception as e:
        logger.warning("分镜 %s 提示词增强失败，回退原始描述: %s", segment.id, e)
        db.rollback()
        fallback = segment.description or ""
        # 原始描述前补风格，尽可能保留一致性信息
        if style_prompt:
            fallback = f"Art style: {style_prompt}\n\n{fallback}"
        neg = _FALLBACK_NEGATIVE
        # 视频回退时同样按风格追加 AI 味负面词（写实/电影全量，非写实裁剪风格冲突词）
        if target == "video":
            neg = f"{neg}, {_video_ai_negatives_for_style(db, project)}".strip(", ")
        # 2026-08-31：multiple people 仅单主体镜追加，群像/多人同框不追加
        if _shot_has_person(segment) and not _shot_is_group(segment):
            neg = f"{neg}, {_SINGLE_SUBJECT_NEGATIVE}".strip(", ")
        return fallback, neg


def clear_enhanced_prompt(db: Session, segment: Segment) -> None:
    """清除分镜增强缓存（分镜描述/资产变更后调用）。"""
    if segment.enhanced_prompt or segment.enhanced_negative_prompt:
        segment.enhanced_prompt = None
        segment.enhanced_negative_prompt = None
        db.commit()
