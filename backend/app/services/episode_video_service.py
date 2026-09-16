"""幕级视频业务服务（P7.6）：LLM 分幕规划 + 真实拆幕 + 一幕一视频。

取代逐镜视频：一幕在生成时按「每幕目标时长」（5/10/15/18s，用户选择）由 LLM 规划成 N 个真实幕
（Episode 行），**一幕一视频**；每幕 prompt 为**幕级连贯叙事**（无「第X镜」时间轴表），
首尾帧图为文生图生成（img2img 资产参考），幕间用同源设计图衔接、剧情 LLM 保证承接。

链路：
1. plan_video_script：LLM 规划 N 幕（splits：shot_indexes/narrative/first_scene/last_scene/title）
2. split_episodes：真实拆幕（原幕=幕1，新建幕2..N，分镜移动归属，每幕写自己的 video_script）
3. build_episode_videos：每幕创建 1 个 EpisodeVideo 行（幕级连贯叙事 prompt，首/尾帧由任务阶段1回填）
4. generate：分幕 → 拆幕 → 建行 → 派发 generate_episode_video 任务
"""
import json
import logging
import math
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.episode_video import EpisodeVideo
from app.models.media import MediaStatus
from app.models.model_config import ModelType
from app.models.project import Episode
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.services.llm_script_service import _extract_json, normalize_episode_title
from app.services.style_service import get_effective_style_prompt
from app.utils.media import delete_media_file

logger = logging.getLogger(__name__)

# 每幕目标时长（用户可选）→ 视频帧数（Agnes num_frames 须 8n+1 且 ≤441）
_PER_DURATION_FRAMES = {5: 121, 10: 241, 15: 361, 18: 441}
DEFAULT_PER_DURATION = 15
# 24fps
FRAME_RATE = 24

# 幕级视频尺寸映射（复用前端 dimsFor 逻辑：项目 aspect_ratio → 视频宽高）。
# 2026-08-16：幕级设计图/视频尺寸统一走 video_service.dims_for_ratio（按项目分辨率
# 480p/720p 档位），不再维护独立 _ASPECT_DIMS（此前固定 768p 档）。
_PLAN_TMPL = """你是短剧导演兼 AI 视频提示词工程师。一幕在单次视频生成时长受限时（每幕目标时长 {target_duration}s），
需要把这一幕的剧情规划成 **{segment_count} 个幕**（真实拆幕，一幕一视频）。
每个幕是一个连贯的剧情片段，幕与幕之间剧情衔接、不割裂：**幕 k 必须承接幕 k-1 的结尾**，
（角色/场景/情绪自然延续，画面连续感强）。每个幕用自然语言写**幕级连贯叙事**——
不要写「第X镜」编号/时间轴表，而是叙述该幕连续发生的画面/动作/运镜/光线。

【分镜信息】（JSON，按 index 顺序）
{segments_json}

【编写规则】
1. 全局设定（scene/characters/light/style 四个字段，整幕统一）：
   - scene：主要场景与氛围（含时间/天气/空间）
   - characters：点名出现的角色，固定其外貌锚点（服装/发型/气质，引用其资产描述，不得跨幕改动）
   - light：统一光影基调（光源方向 + 色调分级）
   - style：原样包含「Art style: {style}」
2. 分幕（splits）：**恰好 {segment_count} 幕**，每幕覆盖一段连续的分镜（shot_indexes 按分镜顺序切幕）；
   每幕时长（分镜 duration 之和）不得超过目标时长 {target_duration}s；
   narrative 用自然语言叙述该幕连续画面（「先…然后…最后…」），动作要慢/轻柔/连贯自然，
   运镜稳（缓慢推镜/平稳跟拍/轻微环绕/固定镜头），禁止快速大幅动作。
3. 每幕给出 first_scene（该幕起始画面描述）与 last_scene（该幕结束画面描述），
   用于生成幕首图/幕尾图；幕 k 的 first_scene 应与幕 k-1 的 last_scene 情节衔接。
4. 每幕给一个 title（如「主幕」/「主幕-2」），体现剧情段落（如「雨夜相逢」「对峙」）。
5. 禁止在描述中写台词/对白/字幕文字（台词由程序另行注入）；禁止写模糊词（"好看/唯美"）。
6. 景别按分镜原文（远景/全景/中景/近景/特写）；情绪氛围影响运镜节奏（紧张=快切、平静=缓推）。

要求返回**纯 JSON**（不要 markdown 代码块、不要任何解释文字），结构：
{{
  "scene": "江南雨巷夜景，青石板路湿亮，暮色深蓝，细雨斜落",
  "characters": "苏尘：青年男子，身形清瘦，墨色长发半束，素白长衫外罩青灰氅衣，气质孤清",
  "light": "暮蓝冷调，冷光自左前方45度斜射入，暗部带蓝紫倾向",
  "style": "Art style: 写实电影风格",
  "splits": [
    {{
      "index": 1,
      "title": "主幕",
      "shot_indexes": [1, 2, 3],
      "narrative": "苏尘撑着油纸伞自巷口缓步走来，雨水沿伞沿滴落，他抬眼穿过雨幕望向巷口深处，攥紧伞柄驻足凝望",
      "first_scene": "雨巷全景，暮蓝冷调，苏尘撑伞的身影出现在巷口",
      "last_scene": "苏尘攥紧伞柄的手部特写，指节泛白，眉头紧锁"
    }},
    {{
      "index": 2,
      "title": "主幕-2",
      "shot_indexes": [4, 5, 6],
      "narrative": "承接上一幕，巷口出现另一道身影，苏尘抬眼凝望，两道人影在雨中相对而立",
      "first_scene": "苏尘抬眼望向巷口深处的特写",
      "last_scene": "两道人影在雨中对望的全景"
    }}
  ]
}}
- shot_indexes 必须覆盖全部输入分镜且不重叠、连续切幕（正好 {segment_count} 幕）
- index 从 1 开始递增；narrative/first_scene/last_scene 均用自然语言，不要出现「第X镜」
- 所有字符串值内禁止使用裸 ASCII 双引号 "；需要引号时一律用中文引号「」
- 只返回 JSON"""


def resolve_r2v_refs(db: Session, project, ref_max: int = 8) -> list[str]:
    """MiniMax H3 R2V 参考图：项目角色资产的多角度视图（四视图全量）。

    对标 Seedance 多锚点做法——多张参考图让模型建立角色"全貌认知"：
    - 每个角色取 four_view_urls 全部 4 张（正面全身 + 45° 侧身 + 背面 + 脸部特写）；
      脸部特写是 Ref2VA 优先读取的锁脸核心视图，必须包含（2026-08-07 规范调整）
    - 最多取 ref_max 张（capability.ref_max 可覆盖，默认 8：新权重节点 9 图上限，
      首帧占 1 位，参考资产最多 8 张），避免显存压力
    - 角色未生成四视图时回退封面，至少返回 1 张（R2V 节点要求 ≥1 参考图）
    """
    from app.models.asset import Asset, AssetType

    if project is None:
        return []
    chars = db.scalar(
        select(Asset)
        .where(Asset.project_id == project.id, Asset.type == AssetType.character)
        .order_by(Asset.created_at.asc())
    )
    if chars is None:
        return []
    # 注意：多角色时取首个角色（主角锚定，与 _resolve_design_refs 一致），避免多角色特征混叠
    refs: list[str] = []
    # 2026-08-09：四视图新链路产出 character_sheet_url（单张四格合一图：半身特征格+
    # 正面/侧面/背面全身，R2V 参考图规格）→ R2V 优先用它；旧 four_view_urls 四张独立
    # 分图仅历史数据回退（新链路不再写入）。
    sheet = getattr(chars, "character_sheet_url", None) or ""
    if sheet:
        refs.append(sheet)
    else:
        views = list(chars.four_view_urls or [])[:4]  # 正面/侧面/背面/特写（锁脸核心）
        if not any(v for v in views):
            views = [chars.cover_url]
        for u in views:
            if u and u not in refs:
                refs.append(u)
    return refs[: max(1, int(ref_max or 8))]


def _resolve_design_refs(db: Session, segment: Segment, *, core_only: bool = False) -> list[str]:
    """幕级设计图参考图：img2img 参考（保证人物/场景一致性）。

    2026-08-07 修复③（关键）：**多图合成必崩**——旧逻辑首帧塞场景+3 个角色全身
    cover+道具（5-6 张）做 img2img，1:1 全身图被强行融入 16:9 场景画布，模型
    多图融合导致手部畸形/脸拉长/整体 morphing（视觉模型实测确认）。
    四视图/关键帧链路早已只用 1 张参考（踩过同坑）。设计图统一收敛为：
    - 首帧/衔接图（core_only=False）：场景 cover + 首个角色（主角）cover，≤2 张
    - 尾帧（core_only=True）：仅首个角色（主角）cover，≤1 张
    其余角色/道具不参与，由 prompt（LLM 增强已写清群像与道具）自行生成，
    模型自由生成人群反而稳定。无资产时返回空列表（任务回退纯文生图）。
    """
    from app.models.asset import Asset

    refs: list[str] = []

    def _add(u):
        if u and u not in refs:
            refs.append(u)

    def _get(aid) -> Asset | None:
        try:
            return db.get(Asset, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            return None

    if not core_only and segment.scene_id:
        a = _get(segment.scene_id)
        if a:
            # 场景参考两张图（2026-08-10）：封面作 ref_image_0 构图锚点（避免网格入画首帧），
            # 六格多视角图第二位补充空间结构。
            if a.cover_url:
                _add(a.cover_url)
            sheet = getattr(a, "scene_sheet_url", None) or ""
            if sheet:
                _add(sheet)
    # 首个角色（主角）锚定外形：首帧/尾帧均保留，保证人物一致性
    cids = list(segment.character_ids or [])
    if cids:
        a = _get(cids[0])
        if a:
            _add(a.cover_url)
    return refs


def target_episode_count(segments: list[Segment], per_duration: int) -> int:
    """分幕目标幕数：整幕总时长 ≤ 目标时长 → 1 幕；否则向上取整（最少拆分）。

    P7.6：一幕一视频；每幕时长由用户选择（5/10/15/18s）。
    """
    total = sum((s.duration or 5.0) for s in segments)
    if total <= per_duration:
        return 1
    return max(2, math.ceil(total / per_duration))


def _fallback_splits(segments: list[Segment], n: int) -> list[dict]:
    """兜底分幕：整幕均分成 n 幕，narrative 拼接分镜描述。"""
    n = min(n, len(segments))
    per = max(1, math.ceil(len(segments) / n))
    splits: list[dict] = []
    for i in range(0, len(segments), per):
        chunk = segments[i : i + per]
        narrative = "，".join(
            (s.description or "").strip() for s in chunk if (s.description or "").strip()
        )
        splits.append({
            "index": len(splits) + 1,
            "title": f"幕{len(splits) + 1}",
            "shot_indexes": [s.index for s in chunk],
            "narrative": narrative,
            "first_scene": (chunk[0].description or "").strip(),
            "last_scene": (chunk[-1].description or "").strip(),
        })
    return splits


def plan_groups(segments: list[Segment], script: dict, per_duration: int = DEFAULT_PER_DURATION) -> list[dict]:
    """分幕（P7.6）：一幕一视频，每幕 1 组（该幕全部分镜）。

    优先使用 LLM script.splits（覆盖全部镜、幕数与 target_episode_count 一致、每幕 ≤目标时长）；
    否则回退均分。返回 [{"segments", "narrative", "first_scene", "last_scene"}]。
    """
    by_index = {s.index: s for s in segments}
    want = target_episode_count(segments, per_duration)

    # 尝试 LLM 分幕（幕数必须正好 = 目标幕数）
    llm_groups: list[dict] = []
    covered: set[int] = set()
    for item in script.get("splits") or []:
        if not isinstance(item, dict):
            continue
        try:
            idxs = [int(i) for i in item.get("shot_indexes") or []]
        except (TypeError, ValueError):
            continue
        members = [by_index[i] for i in idxs if i in by_index]
        if not members:
            continue
        if any(i in covered for i in idxs):
            continue  # 与之前幕重叠 → 非法
        duration = sum((s.duration or 5.0) for s in members)
        if duration > per_duration:
            continue  # 超目标时长该幕非法
        covered.update(idxs)
        llm_groups.append({
            "segments": members,
            "narrative": (item.get("narrative") or "").strip(),
            "first_scene": (item.get("first_scene") or "").strip(),
            "last_scene": (item.get("last_scene") or "").strip(),
        })

    if llm_groups and len(llm_groups) == want and covered == {s.index for s in segments}:
        return llm_groups

    # 兜底：整幕均分成 want 幕
    return [
        {
            "segments": [by_index[i] for i in sp["shot_indexes"]],
            "narrative": sp["narrative"],
            "first_scene": sp["first_scene"],
            "last_scene": sp["last_scene"],
        }
        for sp in _fallback_splits(segments, want)
    ]


def plan_design_images(groups: list[dict], script: dict) -> list[dict]:
    """规划幕级设计图集（P7.2）：每段首图 + 末段尾图，共 N+1 张。

    返回 [{key, segment, scene_desc, narrative, shot_type, role, is_opening, is_closing}]：
    - seg1_first（幕首图）：段1 first_scene
    - seg{k}_first（衔接图）：段 k first_scene（= 段 k-1 尾帧 = 段 k 首帧，同源衔接）
    - seg{N}_last（幕尾图）：末段 last_scene
    """
    designs: list[dict] = []
    n = len(groups)
    for gi, g in enumerate(groups, start=1):
        first = g["segments"][0]
        designs.append({
            "key": f"seg{gi}_first",
            "segment": first,
            "scene_desc": g["first_scene"] or (first.description or "").strip(),
            "narrative": (g.get("narrative") or "").strip(),
            "shot_type": first.shot_type or "",
            "role": "幕首图（整幕起始画面）" if gi == 1 else f"衔接图（段{gi}起始画面，兼作段{gi-1}尾帧）",
            "is_opening": gi == 1,
            "is_closing": False,
        })
        if gi == n:
            last = g["segments"][-1]
            designs.append({
                "key": f"seg{n}_last",
                "segment": last,
                "scene_desc": g["last_scene"] or (last.description or "").strip(),
                "narrative": (g.get("narrative") or "").strip(),
                "shot_type": last.shot_type or "",
                "role": "幕尾图（整幕结束画面）",
                "is_opening": False,
                "is_closing": True,
            })
    return designs


def build_design_image_prompt(
    scene_desc: str,
    style: str | None = None,
    *,
    shot_type: str = "",
    role: str = "",
) -> str:
    """幕级设计图 prompt：起始/结束画面描述 + 镜头语言 + 电影级约束 + 风格。

    这是 LLM 增强失败时的兜底；正常链路走 enhance_design_prompt。
    """
    style_line = f"，Art style: {style}" if style else ""
    shot_line = f"，{shot_type}，静态电影级构图" if shot_type else "，静态电影级构图"
    role_line = f"，{role}" if role else ""
    return (
        f"{scene_desc.strip()}{shot_line}{role_line}，电影级画面，构图完整、细节清晰，"
        f"人物面部结构稳定、五官清晰，自然光影层次，避免平光过曝{style_line}"
    )


# 设计图 LLM 增强兜底负面词（覆盖低质/畸形/多余人/背景文字）
_DESIGN_FALLBACK_NEGATIVE = (
    "low quality, lowres, blurry, jpeg artifacts, watermark, text, subtitle, "
    "logo, signature, extra person, multiple people, duplicate, bad anatomy, "
    "bad hands, missing fingers, extra digits, deformed hands, malformed limbs, "
    "distorted face, deformed face, disfigured, ugly, cross-eyed, cropped, "
    "out of frame, bad proportions, oversmoothed skin, plastic skin, cgi render"
)

_DESIGN_ENHANCE_TMPL = """你是一名电影级 AI 提示词工程师。根据一幕某段的画面描述与资产设定，
扩写为一条精细的图片生成提示词（用于生成该段的幕首图/幕尾图设计图），并给出负面提示词。
该图将作为长视频生成的首帧/尾帧参考图，画质与一致性至关重要。

【画面用途】
{role}

【画面描述】（中文，该段起始或结束那一刻的画面）
{scene_desc}

【镜头语言】
景别：{shot_type}

【段剧情上下文】（该画面所属段落的完整叙事，帮助理解画面所处情节与情绪）
{narrative}

【资产设定】（角色外貌/场景/道具，必须严格引用、不得改动外貌细节）
{assets_block}

【风格】（最高优先级，必须原样包含在 prompt 中，不得遗漏）
Art style: {style}

【返回格式】
只返回纯 JSON（不要 markdown 代码块、不要任何解释文字），结构：
{{"prompt": "精细提示词", "negative_prompt": "负面提示词"}}

【prompt 编写规则】（350~450 词；角色/道具中文名除外）
1. 主体：点名出现的角色与道具（保留中文名），严格引用资产描述中的外貌细节（发型、瞳色、服饰、体型、
   配饰），外貌必须与描述完全一致、不得改动；写清表情与眼神（如：眼神沉静、眉头微锁），避免呆滞空洞；
   强调人物面部结构稳定一致、五官清晰、比例协调
2. 场景：结合场景描述写环境、时间、天气、空间层次（前景/中景/背景）与标志性元素
3. 光照与色调（电影级）：按电影标准设计布光——说明光源类型与方向（如：冷蓝夜戏、暖黄灯笼光、
   侧逆光轮廓）、色调分级，避免平光、避免泛白过曝
4. 构图：按景别写静态电影级构图——主体位置平衡、留白合理、景深层次分明，
   近景/特写聚焦面部与眼神，远景/全景交代环境与人物位置关系
5. 景别：把「{shot_type}」翻译为镜头语言（远景=establishing wide shot、全景=wide shot、
   中景=medium shot、近景=close-up shot、特写=extreme close-up）并写入 prompt
6. 风格（最高优先级）：必须原样包含「Art style: {style}」，并以该风格为核心组织画面
   ——媒介、笔触/渲染方式、色彩、质感、构图全部严格遵循该风格
7. 结尾追加电影级质量词：highly detailed, sharp focus, shallow depth of field,
   cinematic lighting, subtle film grain, 8k uhd, masterpiece quality
8. 除角色/道具中文名外，提示词全部使用英文编写；不得编造上下文中不存在的角色外貌特征

【negative_prompt 规则】
覆盖低分辨率、模糊、噪点、手部畸形、肢体变形、面部崩坏、五官错位、多余人物、
文字、水印、签名、构图裁切、过度平滑、塑料感、AI 感（cgi render, 3d animation,
cel shading）等，英文逗号分隔，20~40 个短语。"""


def _design_prompt_valid(prompt: str, style: str | None) -> bool:
    """设计图 prompt 校验：非空、长度足够、含 Art style。"""
    p = (prompt or "").strip()
    if len(p) < 80:
        return False
    if style and "Art style" not in p:
        return False
    return True


def enhance_design_prompt(
    db: Session,
    scene_desc: str,
    segment: Segment,
    style: str | None,
    *,
    narrative: str = "",
    shot_type: str = "",
    role: str = "",
    core_only: bool = False,
    model_id=None,
) -> tuple[str, str]:
    """幕级设计图 LLM 增强：画面描述 + 段剧情/景别/用途 + 资产设定 → 精细 prompt + 负面词。

    返回 (prompt, negative_prompt)。LLM 失败/输出不合格时回退 build_design_image_prompt（不阻断）。
    core_only=True（尾帧）：资产块只含场景 + 首个角色（主角），与 _resolve_design_refs
    一致，避免 LLM 把全部角色写进画面导致首尾帧全员同框。
    """
    from app.models.asset import Asset

    if not (scene_desc or "").strip():
        return build_design_image_prompt(scene_desc or "", style, shot_type=shot_type, role=role), _DESIGN_FALLBACK_NEGATIVE

    # 收集该镜关联的角色/场景/道具资产描述（与关键帧增强一致的上下文）
    assets_lines: list[str] = []

    def _brief(a: Asset) -> str:
        desc = a.expanded_description or a.description or a.name
        return f"[{a.name}] {desc}"

    def _get(aid) -> Asset | None:
        try:
            return db.get(Asset, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            return None

    if core_only:
        # 尾帧：资产块只保留首个角色（主角）外形设定，场景/道具不写入
        # （scene_desc 已含 last_scene 收束画面），避免 LLM 又写回"族宴厅全员同框"
        cids = list(segment.character_ids or [])
        if cids:
            a = _get(cids[0])
            if a:
                assets_lines.append(f"- 角色：{_brief(a)}")
    else:
        # 首帧/衔接图：只保留场景 + 首个角色（主角）。与 _resolve_design_refs 一致：
        # 多图参考必崩（1:1 全身图强融 16:9 场景导致 morphing），配角/道具一律
        # 不进 prompt、不参考，由模型按 scene_desc 自由生成群像（反而更稳）。
        cids = list(segment.character_ids or [])
        if cids:
            a = _get(cids[0])
            if a:
                assets_lines.append(f"- 角色：{_brief(a)}")
        if segment.scene_id:
            a = _get(segment.scene_id)
            if a:
                assets_lines.append(f"- 场景：{_brief(a)}")
    assets_block = "\n".join(assets_lines) if assets_lines else "- （无资产上下文）"

    prompt_text = _DESIGN_ENHANCE_TMPL.format(
        role=role or "幕级设计图",
        scene_desc=scene_desc.strip(),
        shot_type=shot_type or "中景",
        narrative=(narrative or "").strip() or "（无）",
        assets_block=assets_block,
        style=style or "写实电影风格",
    )
    try:
        from app.services.prompt_enhance_service import _resolve_text_model, _extract_json as _pe_extract_json
        model = _resolve_text_model(db, model_id, "script")
        provider = ProviderRegistry.for_model(model)
        resp = provider.chat([{"role": "user", "content": prompt_text}])
        content = resp["choices"][0]["message"]["content"]
        data = _pe_extract_json(content)
        prompt = (data.get("prompt") or "").strip()
        negative = (data.get("negative_prompt") or "").strip()
        # 输出校验：不合格（过短/漏 Art style）→ 视为失败回退
        if not _design_prompt_valid(prompt, style):
            raise ValueError(f"设计图增强输出不合格（len={len(prompt)}）")
        return prompt, negative or _DESIGN_FALLBACK_NEGATIVE
    except Exception as e:
        logger.warning("[episode_video] 设计图提示词增强失败，回退简单拼接：%s", map_to_chinese(e))
        return build_design_image_prompt(scene_desc, style, shot_type=shot_type, role=role), _DESIGN_FALLBACK_NEGATIVE


def _load_script(episode: Episode) -> dict:
    """解析 episode.video_script JSON 为 dict；缺失/非法返回空 dict。"""
    try:
        data = json.loads(episode.video_script or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _splits_valid(script: dict, segments: list[Segment], per_duration: int) -> bool:
    """splits 是否可直接用于生成：per_duration 匹配、每幕时长 ≤ 目标时长、覆盖全部分镜且不重叠。

    小说改编链路预写的 video_script（splits 单段）也走该校验；LLM 偶发超时幕会被拦截，
    generate 将强制重新规划（真实拆幕），避免生成超长视频。
    """
    if script.get("per_duration") != per_duration:
        return False
    splits = script.get("splits") or []
    if not splits:
        return False
    by_index = {s.index: s for s in segments}
    covered: set[int] = set()
    for sp in splits:
        try:
            idxs = [int(i) for i in sp.get("shot_indexes") or []]
        except (TypeError, ValueError):
            return False
        if not idxs or any(i not in by_index or i in covered for i in idxs):
            return False
        duration = sum((by_index[i].duration or 5.0) for i in idxs)
        if duration > per_duration:
            return False
        covered.update(idxs)
    return covered == {s.index for s in segments}


def _build_speech_block(group: list[Segment]) -> str:
    """段内台词/旁白原句块（模型原生语音：必须用中文逐字说出）。"""
    lines: list[str] = []
    for seg in group:
        for dl in (seg.dialogue_lines or []):
            if not isinstance(dl, dict):
                continue
            text = (dl.get("text") or "").strip()
            if not text:
                continue
            speaker = (dl.get("speaker") or "").strip() or "角色"
            lines.append(f"{speaker}用中文说：「{text}」")
        if seg.narration and seg.narration.strip():
            # 旁白必须是画外音：叙述者不在画面中，画面中角色不得开口念旁白
            lines.append(
                "旁白（画外音，叙述者不在画面中）用中文普通话朗读："
                f"「{seg.narration.strip()}」"
            )
    return "\n".join(lines) if lines else ""


# 音效关键词 → 音效描述（2026-08-10：H3 原生立体声音频，按镜头内容穷举音效引导生成）
_SFX_RULES: list[tuple[tuple[str, ...], str]] = [
    (("枪", "剑", "刀", "戟", "棍", "刺", "劈", "砍", "挥", "击", "打", "攻", "战", "斗",
      "拳", "格挡", "防御", "挥击", "横扫", "劈下", "直刺", "冲击波", "打击", "碰撞", "碎裂", "震荡"),
     "金属兵器碰撞声、兵器破空声、沉重打击闷响、金属长枪挥动震颤声、能量冲击波的爆鸣"),
    (("符文", "仙力", "法阵", "能量", "结印", "屏障", "炸裂", "光效", "法则", "天道", "法阵"),
     "符文能量嗡鸣声、仙力涌动声、能量蓄积的滋鸣、护盾屏障震荡声、能量炸裂声"),
    (("黑雾", "雾气", "浩劫", "触手", "戾气", "烟", "涌动", "翻涌", "席卷"),
     "黑雾翻涌流动声、雾气弥漫的低沉轰鸣、戾气涌动咆哮声"),
    (("风", "沙", "废墟", "战场", "残垣", "碎石", "尘埃", "尘土", "苍凉", "荒芜"),
     "呼啸风声、风沙吹拂摩擦声、废墟空旷回响、碎石滚动声"),
    (("腾跃", "跃起", "落地", "踏地", "奔跑", "冲锋", "突进", "前冲", "蹬地", "凌空"),
     "急促脚步声、蹬地发力声、衣袍猎猎翻飞声、落地闷响"),
    (("喘息", "收势", "收枪", "收力", "缓缓", "微微喘息", "疲惫"),
     "角色急促喘息声、收招后衣甲摩擦声"),
]


def _build_sfx_block(segments: list[Segment]) -> str:
    """按幕内镜头内容聚合生成【音效要求】块（穷举可能存在的音效）。

    H3 原生立体声音频会跟随提示词中的音效描述生成；打斗/动作/场景音效
    描述越具体，出片音效越扎实。2026-08-10：用户要求补全打斗音效与场景音效。
    """
    text = " ".join((s.description or "") for s in segments)
    found: list[str] = []
    for words, sfx in _SFX_RULES:
        if any(w in text for w in words) and sfx not in found:
            found.append(sfx)
    if not found:
        found.append("轻柔环境氛围声")
    return (
        "【音效要求】本段所有音效必须且只能用中文语境生成，逐项呈现在对应画面时刻："
        + "；".join(found)
        + "。禁止静音，禁止只有对白没有环境音与动作音，声音与画面动作严格同步。"
    )


def build_segment_prompt(script: dict, group: dict) -> str:
    """程序化拼接段 prompt（P7.2 幕级连贯叙事）。

    场景/角色/光线/风格来自 LLM 全局设定，剧情为该段 narrative，
    台词原句来自段内分镜；无「第X镜」时间轴表。
    """
    scene = (script.get("scene") or "").strip() or "连续场景，风格统一"
    characters = (script.get("characters") or "").strip() or "角色特征不变"
    light = (script.get("light") or "").strip() or "光影一致，光线稳定"
    style = (script.get("style") or "").strip() or "Art style: 写实电影风格"
    narrative = (group.get("narrative") or "").strip() or "画面自然连贯推进，动作缓慢流畅"

    speech = _build_speech_block(group["segments"])
    parts = [
        "【场景】",
        scene,
        "",
        "【角色】",
        characters,
        "",
        "【光线】",
        light,
        "",
        "【剧情】",
        narrative,
        "",
    ]
    if speech:
        parts += [
            "【语言要求】本段所有角色语音、对白、旁白必须且只能用简体中文（普通话，zh-CN）发出："
            "逐字朗读下面「」内的中文原句，绝对禁止翻译成英语或任何外语、禁止夹杂任何英文单词、"
            "禁止中英混杂；台词一律通过人物说话发声呈现，严禁把台词渲染为画面字幕/气泡/文字。",
            "【台词原句】",
            speech,
            "",
        ]
    # 2026-08-10：H3 原生立体声音频——按幕内镜头内容穷举音效引导生成（打斗/场景音效）
    parts += [
        _build_sfx_block(group["segments"]),
        "",
    ]
    parts += [
        "【画质约束】4K 高清，电影质感，细节清晰，动作自然不僵硬；保持人物面部结构始终稳定不变形、"
        "五官清晰、比例协调，表情自然流畅；画面流畅不抖动，运镜稳定；"
        "禁止文字、字幕、水印、logo、边框。",
        style,
    ]
    return "\n".join(parts)


def plan_video_script(
    db: Session, episode_id, *, per_duration: int = DEFAULT_PER_DURATION, model_id=None,
) -> str | None:
    """LLM 分幕规划（scene/characters/light/style + splits[N幕]），存 episode.video_script。

    失败返回 None（不阻断生成，分幕回退均分）。
    """
    ep = db.get(Episode, episode_id)
    if not ep:
        return None
    segments = list(
        db.scalars(
            select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.index)
        ).all()
    )
    if not segments:
        return None
    style = get_effective_style_prompt(db, ep.project) if ep.project else "写实电影风格"
    seg_count = target_episode_count(segments, per_duration)
    payload = [
        {
            "index": s.index,
            "shot_type": s.shot_type,
            "camera": s.camera,
            "emotion": s.emotion,
            "duration": s.duration,
            "description": (s.description or "")[:200],
        }
        for s in segments
    ]
    prompt = _PLAN_TMPL.format(
        segments_json=json.dumps(payload, ensure_ascii=False),
        style=style,
        segment_count=seg_count,
        target_duration=per_duration,
    )

    try:
        model = _resolve_model(db, model_id, ModelType.text, "storyboard")
        provider = ProviderRegistry.for_model_id(db, model.id)
        resp = provider.chat([{"role": "user", "content": prompt}])
        content = resp["choices"][0]["message"]["content"]
        data = _extract_json(content)
    except Exception as e:
        logger.warning("[episode_video] 分幕规划生成失败（跳过，不阻断）：%s", map_to_chinese(e))
        return None

    by_index = {s.index: s for s in segments}
    out_splits: list[dict] = []
    covered: set[int] = set()
    for item in data.get("splits") or []:
        if not isinstance(item, dict):
            continue
        try:
            idxs = [int(i) for i in item.get("shot_indexes") or []]
        except (TypeError, ValueError):
            continue
        if not idxs or any(i not in by_index or i in covered for i in idxs):
            continue
        covered.update(idxs)
        out_splits.append({
            "index": len(out_splits) + 1,
            "title": (item.get("title") or "").strip() or f"幕{len(out_splits) + 1}",
            "shot_indexes": idxs,
            "narrative": (item.get("narrative") or "").strip(),
            "first_scene": (item.get("first_scene") or "").strip(),
            "last_scene": (item.get("last_scene") or "").strip(),
        })

    # 幕必须覆盖全部镜且幕数 = 目标幕数才有效
    if not out_splits or len(out_splits) != seg_count or covered != {s.index for s in segments}:
        logger.warning(
            "[episode_video] 幕 %s 分幕规划不合格（%d 幕，期望 %d 幕，覆盖 %d/%d），跳过",
            episode_id, len(out_splits), seg_count, len(covered), len(segments),
        )
        return None

    ep.video_script = json.dumps(
        {
            "per_duration": per_duration,
            "scene": (data.get("scene") or "").strip(),
            "characters": (data.get("characters") or "").strip(),
            "light": (data.get("light") or "").strip(),
            "style": (data.get("style") or "").strip(),
            "splits": out_splits,
        },
        ensure_ascii=False,
    )
    db.commit()
    logger.info(
        "[episode_video] 幕 %s 分幕规划已生成：%d 幕（per_duration=%ss）",
        episode_id, len(out_splits), per_duration,
    )
    return ep.video_script


def split_episodes(
    db: Session, episode: Episode, script: dict, segments: list[Segment],
) -> list[Episode]:
    """真实拆幕（P7.6）：原幕=幕1（保留第一组分镜），新建幕2..N，分镜移动归属。

    每幕写自己的 video_script（scene/characters/light/style 共享 + 该幕 splits 段）。
    返回拆幕后全部幕（原幕 + 新建幕，按 index 顺序）。
    """
    from app.models.project import Project

    splits = script.get("splits") or []
    if not splits:
        return [episode]
    by_index = {s.index: s for s in segments}

    project = db.get(Project, episode.project_id)
    # 原幕之后所有幕 index 顺延（为新幕腾出位置）
    tail_eps = db.scalars(
        select(Episode)
        .where(Episode.project_id == episode.project_id, Episode.index > episode.index)
        .order_by(Episode.index.asc())
    ).all()
    new_eps: list[Episode] = []
    for i in range(1, len(splits)):
        sp = splits[i]
        new_ep = Episode(
            project_id=episode.project_id,
            index=episode.index + i,
            title=normalize_episode_title(sp.get("title") or f"{episode.title}-{i + 1}", episode.index + i + 1),
            synopsis=None,
            status="draft",
            video_status="none",
        )
        db.add(new_ep)
        db.flush()
        new_eps.append(new_ep)
    # 尾部幕顺延
    offset = len(splits) - 1
    for ep_ in tail_eps:
        ep_.index += offset
    # 分镜移动：每幕取自己 splits 的分镜，episode_id 改到目标幕，index 重置为幕内顺序
    all_eps = [episode, *new_eps]
    for si, sp in enumerate(splits):
        target_ep = all_eps[si]
        seg_list = [by_index[i] for i in sp.get("shot_indexes") or [] if i in by_index]
        for j, seg in enumerate(seg_list, start=1):
            seg.episode_id = target_ep.id
            seg.index = j
        # 每幕写自己的 video_script（单幕：该幕的 splits 段，供 plan_groups 直接复用）
        target_ep.video_script = json.dumps(
            {
                "per_duration": script.get("per_duration"),
                "scene": script.get("scene") or "",
                "characters": script.get("characters") or "",
                "light": script.get("light") or "",
                "style": script.get("style") or "",
                "splits": [sp],
            },
            ensure_ascii=False,
        )
    db.commit()
    return all_eps


def _delete_old_rows(db: Session, episode: Episode) -> None:
    """删除该幕旧段行（DB 行 + 磁盘视频文件 + 取消未完成任务）。"""
    for row in list(episode.episode_videos):
        if row.task_id:
            t = db.get(Task, row.task_id)
            if t and t.status in (TaskStatus.pending, TaskStatus.running):
                from app.services.task_service import sync_cancel_provider

                sync_cancel_provider(db, t)
                t.status = TaskStatus.cancelled
                t.error = "幕级视频已重新生成"
        delete_media_file(row.video_url)
        db.delete(row)
    if episode.episode_videos:
        db.flush()


def build_episode_videos(
    db: Session, episodes: list[Episode], model, width: int, height: int,
    per_duration: int = DEFAULT_PER_DURATION,
) -> list[EpisodeVideo]:
    """每幕创建 1 个 EpisodeVideo 行（P7.6：一幕一视频）。

    首/尾帧不在此处解析——幕级设计图由生成任务阶段1 产出后回填。
    返回行列表（与 episodes 顺序对应，每幕 1 行）。
    """
    frames = _PER_DURATION_FRAMES.get(per_duration, 361)
    rows: list[EpisodeVideo] = []
    for ep in episodes:
        script = _load_script(ep)
        segments = list(
            db.scalars(
                select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)
            ).all()
        )
        groups = plan_groups(segments, script, per_duration)
        if not groups:
            continue
        row = EpisodeVideo(
            episode_id=ep.id,
            index=1,  # 每幕 1 行
            prompt=build_segment_prompt(script, groups[0]),
            num_frames=frames,
            frame_rate=FRAME_RATE,
            width=width,
            height=height,
            status=MediaStatus.pending,
            model_id=model.id,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def _prepare_episode_structure(db: Session, episode_id, per_duration: int) -> tuple[list[Episode], dict]:
    """分幕规划（缺失/失效则 LLM 重规划）+ 真实拆幕，返回 (episodes, script)。

    P7.7：出图任务与视频任务共用。episodes = 原幕(=幕1) + 新建幕2..N。
    """
    ep = db.get(Episode, episode_id)
    if not ep:
        raise ValueError("幕不存在")
    segments = list(
        db.scalars(
            select(Segment).where(Segment.episode_id == episode_id).order_by(Segment.index)
        ).all()
    )
    if not segments:
        raise ValueError("幕内没有分镜，无法生成幕级视频")
    script = _load_script(ep)
    if not _splits_valid(script, segments, per_duration):
        plan_video_script(db, episode_id, per_duration=per_duration)
        db.refresh(ep)
        script = _load_script(ep)
    episodes = split_episodes(db, ep, script, segments)
    return episodes, script


def generate_designs(
    db: Session, episode_id, *, per_duration: int = DEFAULT_PER_DURATION, model_id=None,
):
    """幕首/幕尾图生成入口（P7.7 阶段1）：分幕 → 拆幕 → 每幕建行 → 派发设计图任务。

    只生成幕级设计图（幕首图/衔接图/幕尾图），不生成视频；用户确认首尾帧后
    再点「生成幕级视频」走 generate（阶段2）。返回 (rows, task, episodes)。
    """
    from app.tasks.generate_episode_video import generate_episode_design_task

    per_duration = int(per_duration or DEFAULT_PER_DURATION)
    if per_duration not in _PER_DURATION_FRAMES:
        raise ValueError("每幕时长仅支持 5/10/15/18 秒")

    episodes, _ = _prepare_episode_structure(db, episode_id, per_duration)

    model = _resolve_model(db, model_id, ModelType.video, "video")
    # 项目级视频分辨率（480p/720p）：幕级设计图/视频尺寸按项目分辨率档位（2026-08-16）
    from app.services.video_service import dims_for_ratio
    proj = episodes[0].project if episodes else None
    width, height = dims_for_ratio(
        proj.aspect_ratio if proj else None,
        proj.resolution if proj else None,
    )

    for e in episodes:
        _delete_old_rows(db, e)
    rows = build_episode_videos(db, episodes, model, width, height, per_duration)
    if not rows:
        raise ValueError("分幕失败：幕内没有可分镜的镜头")

    for e in episodes:
        e.video_status = "running"
    task = Task(
        project_id=episodes[0].project_id,
        type=TaskType.generate_episode_design,
        target_type="episode",
        target_id=episodes[0].id,
        model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    for row in rows:
        row.task_id = task.id
    db.commit()
    db.refresh(task)
    generate_episode_design_task.delay(str(task.id))
    logger.info(
        "[episode_video] 幕 %s 已派发幕首/幕尾图任务 task=%s 幕数=%d 每幕时长=%ss",
        episode_id, task.id, len(rows), per_duration,
    )
    return rows, task, episodes


def generate(
    db: Session, episode_id, *, per_duration: int = DEFAULT_PER_DURATION, model_id=None,
):
    """幕级视频生成入口（P7.7 阶段2）：要求首尾帧图已生成，只生成视频。

    与 generate_designs 分离：必须先点「生成幕首/幕尾图」产出首尾帧（阶段1），
    否则抛 ValueError（前端 400 提示先出图）。返回 (rows, task, episodes)。
    """
    from app.tasks.generate_episode_video import generate_episode_video_task

    per_duration = int(per_duration or DEFAULT_PER_DURATION)
    if per_duration not in _PER_DURATION_FRAMES:
        raise ValueError("每幕时长仅支持 5/10/15/18 秒")

    episodes, _ = _prepare_episode_structure(db, episode_id, per_duration)

    # 校验每幕首尾帧已生成（阶段1 产物），否则拒绝生成视频
    for e in episodes:
        ep_rows = list_by_episode(db, e.id)
        if not ep_rows or any(not r.first_frame_url for r in ep_rows):
            raise ValueError("请先点「生成幕首/幕尾图」生成首尾帧，再生成幕级视频")

    model = _resolve_model(db, model_id, ModelType.video, "video")
    # 复用已有行（首尾帧已回填），不重建、不删除
    rows = [r for e in episodes for r in list_by_episode(db, e.id)]

    for e in episodes:
        e.video_status = "running"
    task = Task(
        project_id=episodes[0].project_id,
        type=TaskType.generate_episode_video,
        target_type="episode",
        target_id=episodes[0].id,
        model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    for row in rows:
        row.task_id = task.id
    db.commit()
    db.refresh(task)
    generate_episode_video_task.delay(str(task.id))
    logger.info(
        "[episode_video] 幕 %s 已派发幕级视频任务 task=%s 幕数=%d 每幕时长=%ss",
        episode_id, task.id, len(rows), per_duration,
    )
    return rows, task, episodes


def list_by_episode(db: Session, episode_id) -> list[EpisodeVideo]:
    return db.scalars(
        select(EpisodeVideo)
        .where(EpisodeVideo.episode_id == episode_id)
        .order_by(EpisodeVideo.index.asc())
    ).all()
