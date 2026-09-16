"""LLM 剧本生成：一句话 → 多幕 + 分镜 + 资产清单 + 自动关联。

核心流程：
1. generate_draft：调用 LLM 返回结构化草案（assets + episodes + segments）
2. materialize_draft：落库 Project → Episodes → Assets（查找或创建）→ Segments（按名称关联 asset_id）

资产关联策略（_find_or_create_asset）：
当前项目按 (lower(name), type) 查找 → 复用；找不到 → 创建项目级资产（project_id 必填）。
资产跟项目，不跨项目复用；续集一致性通过在各项目内重新创建同名资产保证。
"""
import json
import logging
import re
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetType
from app.models.media import MediaStatus
from app.models.model_config import ModelType
from app.models.project import Episode, Project, ProjectStatus
from app.models.segment import Segment
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.services.shot_beats import normalize_shot_beats

_NEW_PROMPT_TMPL = """你是一名短剧编剧。根据用户的一句话梗概，生成一部短剧的结构化草案。

要求返回**纯 JSON**（不要 markdown 代码块、不要任何解释文字），结构如下：
{{
  "title": "短剧标题，10字以内",
  "synopsis": "一句话故事梗概",
  "script": "完整剧本文本，包含全部对白与旁白",
  "assets": [
    {{
      "type": "character|scene|prop",
      "name": "资产名称（角色名/场景名/道具名，须与 segments 中引用的名称严格一致）",
      "description": "一句话描述：角色写外貌性别年龄服饰、场景写地点氛围、道具写外观"
    }}
  ],
  "episodes": [
    {{
      "title": "第N幕 标题",
      "synopsis": "本幕剧情概要",
      "segments": [
        {{
          "title": "本镜标题：用 4 个字概括本镜看点/行动（如「林澈拦路」「天台对峙」），禁止出现「画面描述」「镜头」等词，禁止带冒号前缀",
          "shot_type": "远景|全景|中景|近景|特写",
          "camera": "固定|推|拉|摇|移|跟",
          "shot_beats": "可选：镜头内有明确时间分节（动作/情绪/机位变化）时填本镜「分镜内多镜头运镜节拍」——每项 {{"start_sec": 秒, "end_sec": 秒, "shot_type": "远景|全景|中景|近景|特写", "camera": "固定|推|拉|摇|移|跟", "content": "该时段画面：写具体动作动词与运镜（如：猛地推门而入/攥拳抬头、镜头推近）"}}，各段时间连续、无缝覆盖 0~duration（首拍从 0 起、末拍止于 duration），拍数 2~3 个；纯单一运镜/单一景别或无明确分节的镜头填 []；范例：[{{"start_sec": 0.0, "end_sec": 2.0, "shot_type": "近景", "camera": "推", "content": "林浅推门而入"}}, {{"start_sec": 2.0, "end_sec": 4.0, "shot_type": "中景", "camera": "摇", "content": "与星野对视"}}, {{"start_sec": 4.0, "end_sec": 6.0, "shot_type": "全景", "camera": "拉", "content": "落座桌边"}}]";
          "description": "直接写画面本身（不要带任何前缀标签）：写清楚拍什么、视觉细节、出现的角色/场景/道具；动作化写作——有动作的镜头写 ≥2 个具体动词（推门/攥拳/抬头/踉跄/转身/抬手）按时间顺序展开（先…然后…）、给力度与重量感（猛地/缓缓/踉跄），运镜写「类型+幅度+速度」（缓慢推近至特写/快速横移/低角度仰拍）；禁止用情绪形容词替代动作（如「悲伤地离开」要改成「攥紧包带，低头快步走过」）；纯静止气氛镜必须补环境微动态（衣摆/发丝/烟雾/光线变化）让画面「在呼吸」",
          "dialogue_lines": [
            {{"kind": "dialogue|inner", "speaker": "说话角色名", "text": "台词原文", "emotion": "愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊"}}
          ],
          "narration": "旁白内容（仅第1幕第1镜可填，其余必须为空字符串）",
          "emotion": "本镜整体氛围：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊",
          "duration": 8.0,
          "characters": ["角色名"],
          "scene": "场景名",
          "props": ["道具名"],
          "action_sequence": "动作序列标记：本镜属于打斗/动作段时填 as_1、as_2 等序号（同一动作段连续分镜必须用同一标记），否则填 null"
        }}
      ]
    }}
  ]
}}

约束：
- 幕数 1~3 个，每幕分镜 4~8 个
- **duration 由你根据镜头内容在 1~{per_duration} 秒内自动配置（单位：秒，建议整数）**——
  镜头信息量越大、动作/情绪越重时长越长（如 8~15s），简短过场/快节奏对白镜可短（如 5~6s）；
  每镜 duration 必须能读完该镜全部对白（中文真实对话约 5.5 字/秒），即 5s≈28字、8s≈44字、
  10s≈55字、15s≈82字。不要再把所有分镜写成相同时长，按内容高低起伏分配
- 对白（dialogue_lines）每镜总字数 ≈ 该镜 duration × 5.5（5s 镜 ≤30 字，10s 镜 ≤55 字，
  15s 镜 ≤82 字）
- 重要：若某镜对白超过其 duration 可容纳字数，必须拆成多个连续分镜或适当调长 duration，
  拆分后镜头机位/景别可微调，保持叙事连贯；单个分镜 duration 不得超过 {per_duration} 秒
- dialogue_lines 每项 kind 二选一：
  - "dialogue"：角色开口说出的对白（画面中对口型）
  - "inner"：角色内心独白（角色心理想法，用角色本人声音以画外音方式说出，
    画面中该角色嘴唇不动、不得对口型）——用于表达角色心理活动，优先于旁白
- 对白口语化硬要求：对白必须像真实人物对话——短句（单句 ≤12 字）、生活化语气词
  （唉/哼/得/嘛/呢/啊）、允许抢白与打断、避免书面语、避免文绉绉古风修辞、
  避免四字成语堆砌、避免"终于/于是/然而/仿佛"等书面连接词；读起来像人话而不是念诗
- 内心独白同样口语化，带情绪口吻，长度不超对白
- 旁白（narration）仅第 1 幕第 1 镜可用 1 句交代故事背景，其余分镜一律为空字符串；
  心理内容一律用内心独白表达，不用旁白
- assets 中所有角色/场景/道具必须在至少一个 segment 中被引用；反之 segments 中引用的名称必须在 assets 中存在
- 同名同类资产只声明一次（角色"林浅"在第1幕和第2幕都出现，只在 assets 中列一次）
- dialogue_lines 中每个 speaker 必须在 characters 中；同镜多角色对白按剧情顺序排列
- 纯画面镜（无对白/内心独白/旁白）dialogue_lines 为空数组 [] 且 narration 为空字符串
- action_sequence 动作序列标记规则（多镜头动作展示）：
  - 打斗/动作/追逐等"需要连续多镜头才能展示完整"的动作场面，把该段拆成 ≥2 个连续分镜，
    每镜 description 写明动作节拍（起手/逼近/交锋/变招/收势等），并打同一 action_sequence 标记
    （as_1、as_2…按幕内出现顺序编号）
  - 同一动作段的连续分镜必须用同一标记；不同动作段用不同标记；分镜之间不得插入无关镜头
  - 非动作镜（文戏/对话/抒情）action_sequence 一律填 null
  - 动作段分镜与普通分镜一样遵守对白 ≤ duration×5.5 字等约束，可含打斗音效/喝声但避免长对白
- shot_beats 分镜内多镜头运镜节拍规则：
  - 仅当单镜内有明确的时间分节（动作递进、情绪转折、机位/景别切换、景别递进等）才填，否则 []；
    纯单一运镜/单一景别的普通镜头不要为了填而填——分镜内切镜只在确有节拍时使用
  - 每项必须含 start_sec/end_sec（秒，可精确到 0.1）、shot_type、camera、content 四要素；
    时间必须连续且无缝覆盖 0~duration：首拍 start_sec=0，末拍 end_sec=duration，中间拍
    结束时间 = 下一拍开始时间，禁止时间空档、重叠或超出本镜 duration
  - 拍数 2~3 个最稳；超过 3 个的节拍应拆成多个连续分镜（遵守对白 ≤ duration×5.5 字等约束）
  - 对白放在对白发生的节拍之内（如第 2 拍两人对视说话，对白语义必须与第 2 拍 content 匹配），
    节拍时长之和 = duration
- **动作化写作规范（运镜与真实感，description 与 shot_beats[].content 通用）**：
  - 有动作的镜头写 ≥2 个具体动作动词（推门/攥拳/抬头/踉跄/转身/抬手），按时间顺序展开（先…然后…），给力度与重量感（猛地/缓缓/踉跄/强撑）
  - 运镜写明「类型+幅度+速度」（缓慢推近至特写/快速横移/低角度仰拍），禁止只写运镜名不写节奏
  - 禁止用情绪形容词替代动作（如「悲伤地离开」→ 改「攥紧包带，低头快步走过」）；「眺望/凝视/微笑」是动作不是状态，须写明注视对象
  - 纯静止气氛镜（对峙屏息/冥想/凝视）允许静止，但必须补 1~2 处环境微动态（衣摆/发丝/烟雾/光线变化），证明画面「在呼吸」
- emotion 与 dialogue_lines[].emotion 取值必须在枚举内：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊
- 情绪须与对白内容、剧情氛围匹配（如争吵→愤怒/紧张，告别→悲伤，重逢→欢快/温馨）
- 所有字符串值内禁止使用裸 ASCII 双引号 "；需要引号时一律用中文引号「」或转义为 \\"（如 "如果\"它\"存在" 或 "如果「它」存在"）
- 只返回 JSON

用户梗概：{synopsis}
{episode_hint}"""


def _repair_unescaped_quotes(s: str) -> str:
    """修复 JSON 字符串值内未转义的 ASCII 双引号。

    LLM 生成中文剧本时常在字符串值内使用裸 ASCII 双引号（如：如果"它"真的存在），
    导致 json.loads 把它误判为字符串结束符，报 "Expecting ',' delimiter"。

    判定规则：当 " 的前后都是"内容字符"（非结构性字符：空白与 , : { } [ ] " ' `），
    视为内容引号，转义为 \\"。结构性引号（字符串边界）的前后必然是结构性字符，
    不会被误转义。
    """
    content_char = r"[^\s,:{\[\]}\"'`]"
    # 前后都是内容字符的 " 视为内容引号，转义
    s = re.sub(f"(?<={content_char})\"(?={content_char})", r'\\"', s)
    return s


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON（兼容 markdown fence 与前后杂字）。

    踩坑：LLM 常在字符串值内放裸换行符（\\n），标准 json.loads 会报
    "Invalid control character"。用 strict=False 允许控制字符。
    另一常见问题：LLM 在数组/对象末尾留尾随逗号（trailing comma），
    标准 json.loads 同样报错，用正则去掉。
    再一常见问题：LLM 在字符串值内放裸 ASCII 双引号（如 "它"），会报
    "Expecting ',' delimiter"。首次解析失败时调用 _repair_unescaped_quotes
    转义内容引号后重试。
    """
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError(
            f"LLM 输出不含 JSON 对象（未找到 {{}}），可能为拒答或纯文本：{s[:200]!r}"
        )
    s = s[i : j + 1]
    # 去掉尾随逗号：},] 或 },} 或 ],} 等（LLM 常见手误）
    s = re.sub(r",\s*([}\]])", r"\1", s)
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        # 容错：修复字符串值内未转义的 ASCII 双引号后重试
        return json.loads(_repair_unescaped_quotes(s), strict=False)


def _find_or_create_asset(
    db: Session,
    *,
    name: str,
    type_: AssetType,
    description: str | None,
    project_id_for_new,
) -> Asset:
    """查找或创建项目级资产：按 (lower(name), type) 在当前项目内查，找不到则创建。

    资产跟项目（不做全局复用），续集一致性通过在各项目内重新创建同名资产保证。

    并发安全：创建时用 SAVEPOINT（begin_nested），若另一请求已抢先插入同名
    资产触发唯一约束冲突，自动回滚 savepoint 并重新查找复用，不影响外层事务。
    """
    name_lower = name.lower()

    def _query_project():
        if project_id_for_new is None:
            return None
        return db.scalars(
            select(Asset).where(
                Asset.project_id == project_id_for_new,
                func.lower(Asset.name) == name_lower,
                Asset.type == type_,
            ).limit(1)
        ).first()

    # 1) 当前项目查找
    asset = _query_project()
    if asset:
        return asset
    # 2) 创建（SAVEPOINT 防并发竞态时回滚整个外层事务）
    asset = Asset(
        project_id=project_id_for_new,
        type=type_,
        name=name,
        description=description,
        status=MediaStatus.pending,
    )
    try:
        with db.begin_nested():
            db.add(asset)
            db.flush()
    except IntegrityError:
        # 并发竞态：另一请求已抢先插入同名资产，重新查找复用
        asset = _query_project()
        if asset is None:
            raise  # 唯一约束报错却查不到，属异常情况
    # 2026-08-22 全局资产库：项目归属同时写入绑定表（项目删除时资产解绑保留）
    if asset.id and project_id_for_new is not None:
        from app.models.asset import ProjectAsset
        bind_exists = db.scalar(
            select(ProjectAsset).where(
                ProjectAsset.asset_id == asset.id,
                ProjectAsset.project_id == project_id_for_new,
            )
        )
        if bind_exists is None:
            db.add(ProjectAsset(asset_id=asset.id, project_id=project_id_for_new))
    db.commit()
    db.refresh(asset)
    return asset


def _norm_name(name: str) -> str:
    """资产名归一化：strip + lower，用于 asset_map key 与查找。"""
    return (name or "").strip().lower()


def _build_episode_script(
    db: Session,
    ep: Episode,
    ep_data: dict,
    asset_map: dict[tuple[str, str], str],
    per_duration: int,
    style_frag: str,
) -> str:
    """为单幕预写 video_script（P7.6）：splits 单段 + 幕级叙事 + 每幕目标时长。

    scene/characters 从该幕分镜引用的资产描述聚合（保证人物/场景锚点稳定）；
    light 走统一电影级布光；style 用项目有效风格片段（Art style 前缀）。
    narrative/first_scene/last_scene 优先用改编 LLM 输出，缺省回退分镜描述拼接。
    """
    segments = ep_data.get("segments") or []
    scene_names: list[str] = []
    char_names: list[str] = []
    for seg in segments:
        sn = (seg.get("scene") or "").strip()
        if sn and sn not in scene_names:
            scene_names.append(sn)
        for c in seg.get("characters") or []:
            cn = (c or "").strip()
            if cn and cn not in char_names:
                char_names.append(cn)

    def _asset_desc(atype: str, name: str) -> str:
        aid = asset_map.get((atype, _norm_name(name)))
        if not aid:
            return name
        try:
            a = db.get(Asset, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            return name
        if a is None:
            return name
        return f"{name}：{a.description or a.name}"

    scene_desc = "；".join(_asset_desc("scene", n) for n in scene_names) or "连续场景，风格统一"
    chars_desc = "；".join(_asset_desc("character", n) for n in char_names) or "角色特征不变"

    narrative = (ep_data.get("narrative") or "").strip()
    first_scene = (ep_data.get("first_scene") or "").strip()
    last_scene = (ep_data.get("last_scene") or "").strip()
    if not narrative:
        narrative = "，".join(
            (s.get("description") or "").strip() for s in segments if (s.get("description") or "").strip()
        )
    if not first_scene and segments:
        first_scene = (segments[0].get("description") or "").strip()
    if not last_scene and segments:
        last_scene = (segments[-1].get("description") or "").strip()

    return json.dumps(
        {
            "per_duration": per_duration,
            "scene": scene_desc,
            "characters": chars_desc,
            "light": "自然光影，电影级布光，光线稳定统一",
            "style": f"Art style: {style_frag}",
            "splits": [
                {
                    "index": 1,
                    "title": ep.title,
                    "shot_indexes": list(range(1, len(segments) + 1)),
                    "narrative": narrative,
                    "first_scene": first_scene,
                    "last_scene": last_scene,
                }
            ],
        },
        ensure_ascii=False,
    )


# 2026-08-16：分镜时长策略（用户拍板「LLM 按剧本内容自动拆分分镜时长，最长 15 秒」）。
# - 旁白（narration）不注入视频音频（不读）
# - duration 由 LLM 根据镜头内容在 1~per_duration（默认上限 15s）内自动配置，
#   LTX-2.5 / MiniMax H3 均支持 5~15s 时长（帧数 8n+1 档位），不再统一强制 5 秒
# - 对白超过该镜 duration 可容纳量（约 5.5 字/秒）时：LLM 优先自行拆分/调时长，
#   落库时按动态阈值兜底拆分（见 _split_overlong_segments）
# 2026-08-10 实测：真实对话语速约 5~6 字/秒（此前 4 字/秒偏慢、像朗诵）
_CHARS_PER_SEC = 5.5
# 视频最短时长（模型硬约束：LTX25/MiniMax H3 均要求 ≥5s）
VIDEO_MIN_SECONDS = 5.0
# 视频最长时长（默认上限，per_duration 可覆盖）
VIDEO_MAX_SECONDS = 15.0
# 兜底分镜时长（LLM 未给 duration 或缺省时）
VIDEO_SECONDS = 5.0
# 5 秒镜对白字数上限基准（中文真实对话约 5.5 字/秒 → 30 字）；长镜按 duration×5.5 放宽
MAX_DIALOGUE_CHARS_5S = 30


def _clean_segment_description(raw):
    """兜底清理分镜描述前缀（LLM 偶发把字段标签写进值）：剔除「画面描述：」等前缀。"""
    import re as _re
    if not raw or not str(raw).strip():
        return raw
    text = str(raw).strip()
    m = _re.match(r"^(?:\[?(?:[\u4e00-\u9fa5\w\s]+)?\]?)?(?:画面描述|镜头描述|分镜描述|镜头|画面)\s*[:：.。]、?\s*", text)
    if m:
        text = text[m.end():].strip()
    return text or None


def calibrate_segment_duration(seg_data: dict, per_duration: int | None) -> float:
    """分镜时长校正（落库时调用）：信任 LLM 按镜头内容配置的时长，收敛到合法区间。

    2026-08-16 新规则（用户拍板「LLM 按剧本内容自动拆分分镜时长，最长 15 秒」）：
    - LLM 未给 duration / 非法 → 兜底 5.0 秒
    - 收敛到 [VIDEO_MIN_SECONDS(5), per_duration(默认15)]——与 LTX25/MiniMax 模型
      时长约束一致（低于 5s 会被 provider 强行拉长，导致对白/内容比例失衡）
    """
    try:
        raw = float(seg_data.get("duration"))
    except (TypeError, ValueError):
        return VIDEO_SECONDS
    if not raw or raw <= 0:
        return VIDEO_SECONDS
    cap = float(per_duration or VIDEO_MAX_SECONDS)
    return max(VIDEO_MIN_SECONDS, min(cap, raw))


def _split_long_line(line: dict, max_chars: int = MAX_DIALOGUE_CHARS_5S) -> list[dict]:
    """超长单句按标点递归拆成多个对白行（保留原 speaker/emotion）。

    2026-08-09（方案B）：单句超过 5 秒朗读量（>20 字）且 LLM 未拆分时，
    先按句末标点「。！？；;」切成语义子句；拆出的子句若仍超限（如单句内
    只有结尾句号、或子句本身很长）再按逗号/顿号「，、,」递归拆一层，
    直到子句 ≤ 上限或不可再拆（无标点长句，保留原样不破坏台词完整性）。
    """
    text = (line.get("text") or "").strip()
    parts = [p.strip() for p in re.findall(r"[^。！？；;]+[。！？；;]?", text) if p.strip()]
    if len(parts) > 1:
        return _flatten_subs(line, parts, max_chars)
    # 整句超限且无句末切分点：按逗号/顿号再拆一层
    if len(text) > max_chars:
        sub = [p.strip() for p in re.split(r"[，、,]", text) if p.strip()]
        if len(sub) > 1:
            return _flatten_subs(line, sub, max_chars)
    return [line]


def _flatten_subs(line: dict, parts: list[str], max_chars: int) -> list[dict]:
    """把拆出的子句逐段检查：仍超限的子句递归再拆，其余直接返回。"""
    out: list[dict] = []
    for p in parts:
        if len(p) > max_chars:
            out.extend(_split_long_line({**line, "text": p}, max_chars))
        else:
            out.append({**line, "text": p})
    return out


def _split_overlong_segments(seg_data: dict) -> list[dict]:
    """对白超限分镜自动拆分（2026-08-09 方案B，2026-08-16 阈值随 duration 动态）。

    背景：LLM 提示词「对白 ≤ duration×5.5 字、超限拆镜」是软约束，模型常违规；
    方案B：落库时按字数把超长分镜自动切成多个连续分镜，每条链路（剧本生成/
    剧本改编）统一在 materialize_draft 生效。

    规则（2026-08-16 起阈值动态化）：
    - 阈值 = max(MAX_DIALOGUE_CHARS_5S(30), round(duration × 5.5))——长镜（10s/15s）
      可容纳更多对白，不再一律按 30 字拆镜
    - 对白总字数 ≤ 阈值 → 原样返回
    - 超限 → 按句贪心分组（每组 ≤ 阈值）；单句超限先按标点拆句
    - 拆分镜复制原镜场景/角色/道具/情绪，narration 归第一镜
    - 后续镜 description 追加接续说明（同一场景、机位微调），避免画面重复
    - 仍超限的孤立长句（无标点）保留原样并告警（无法再拆）
    """
    lines = [
        l for l in (seg_data.get("dialogue_lines") or [])
        if isinstance(l, dict) and (l.get("text") or "").strip()
    ]
    if not lines:
        return [seg_data]
    try:
        dur = float(seg_data.get("duration") or VIDEO_SECONDS)
    except (TypeError, ValueError):
        dur = VIDEO_SECONDS
    max_chars = max(MAX_DIALOGUE_CHARS_5S, int(round(max(dur, 1.0) * _CHARS_PER_SEC)))
    total = sum(len((l.get("text") or "").strip()) for l in lines)
    if total <= max_chars:
        return [seg_data]

    # 1) 展开：超长单句先按标点拆句
    expanded: list[dict] = []
    for line in lines:
        if len((line.get("text") or "").strip()) > max_chars:
            expanded.extend(_split_long_line(line, max_chars))
        else:
            expanded.append(line)

    # 2) 贪心分组：每组总字数 ≤ 阈值
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_n = 0
    for line in expanded:
        n = len((line.get("text") or "").strip())
        if n > max_chars:
            # 拆句后仍超限（无标点超长句）：独立成镜并告警
            if cur:
                groups.append(cur)
                cur, cur_n = [], 0
            groups.append([line])
            logging.warning(
                "分镜对白单句 %d 字无法拆分（无句末标点），将整句保留：%r",
                n, (line.get("text") or "")[:40],
            )
            continue
        if cur_n + n > max_chars and cur:
            groups.append(cur)
            cur, cur_n = [], 0
        cur.append(line)
        cur_n += n
    if cur:
        groups.append(cur)

    if len(groups) <= 1:
        return [seg_data]

    # 3) 构造拆分镜：复制原镜字段，对白按组分发，narration 归第一镜
    base = {k: v for k, v in seg_data.items() if k != "dialogue_lines"}
    base_desc = (seg_data.get("description") or "").strip()
    out: list[dict] = []
    for gi, g in enumerate(groups):
        piece = dict(base)
        piece["dialogue_lines"] = g
        # 2026-08-28：拆分后各镜时长改变，原 shot_beats 时间段不再有效 —— 重置为空
        #（由 LLM/用户按拆后时长重建节拍），避免过时分段注入增强层造成切镜错位
        piece.pop("shot_beats", None)
        if gi == 0:
            piece["narration"] = seg_data.get("narration")
        else:
            piece["narration"] = ""
            piece["description"] = (
                f"{base_desc} 本镜接续上一镜台词：保持同一场景、角色站位与光线，"
                "镜头机位/景别做小幅调整（如切近景、反打或过肩），动作自然延续。"
            ).strip()
        out.append(piece)
    logging.info(
        "[剧本] 对白超限分镜自动拆分为 %d 镜（原对白 %d 字，时长 %ss，上限 %d 字）",
        len(out), total, dur, max_chars,
    )
    return out


def _link_segment(segment: Segment, *, seg_data: dict, asset_map: dict[tuple[str, str], str]) -> None:
    """根据 LLM 返回的 characters/scene/props 名称，把 asset_id 写入 segment 字段。

    同时回填 dialogue_lines[].character_id（按 speaker 名 → asset_map）。
    emotion 落 segment.emotion。
    dialogue 字符串字段由 dialogue_lines 拼接生成（兼容旧展示）。

    名称匹配统一走 _norm_name 归一化（strip + lower），避免 LLM 返回的
    speaker 与角色名大小写/空格不一致导致 character_id 误判为 None。
    """
    char_names = seg_data.get("characters") or []
    scene_name = seg_data.get("scene")
    prop_names = seg_data.get("props") or []

    # character_ids：归一化匹配，未匹配的记录警告
    char_ids: list[str] = []
    for n in char_names:
        key = ("character", _norm_name(n))
        if key in asset_map:
            char_ids.append(asset_map[key])
        else:
            logging.warning("分镜角色名未匹配到资产：%r（已归一化为 %r）", n, _norm_name(n))
    segment.character_ids = char_ids

    if scene_name:
        key = ("scene", _norm_name(scene_name))
        if key in asset_map:
            segment.scene_id = asset_map[key]
        else:
            logging.warning("分镜场景名未匹配到资产：%r", scene_name)

    prop_ids: list[str] = []
    for n in prop_names:
        key = ("prop", _norm_name(n))
        if key in asset_map:
            prop_ids.append(asset_map[key])
        else:
            logging.warning("分镜道具名未匹配到资产：%r", n)
    segment.prop_ids = prop_ids

    # 结构化对白：回填 character_id + 写入 segment.dialogue_lines
    raw_lines = seg_data.get("dialogue_lines") or []
    norm_lines: list[dict] = []
    for line in raw_lines:
        if not isinstance(line, dict):
            continue
        speaker = (line.get("speaker") or "").strip()
        text = (line.get("text") or "").strip()
        if not speaker or not text:
            continue
        cid = asset_map.get(("character", _norm_name(speaker)))
        if cid is None:
            logging.warning(
                "对白 speaker 未匹配到角色资产：%r（分镜描述前30字：%r）",
                speaker, (segment.description or "")[:30],
            )
        norm_lines.append({
            "speaker": speaker,
            "text": text,
            "emotion": (line.get("emotion") or "").strip() or None,
            "character_id": cid,
            # 2026-08-10：对话类型——dialogue（开口对白）/ inner（内心独白，本人声音画外音、嘴唇不动）
            "kind": (line.get("kind") or "dialogue").strip() or "dialogue",
        })
    segment.dialogue_lines = norm_lines
    segment.emotion = (seg_data.get("emotion") or "").strip() or None
    # 2026-08-22 分镜标题（4 字概括）：LLM 产出或导入剧本时生成；缺失则从描述取 4 字兜底。
    # 兜底前剔除常见描述前缀（"画面描述：""镜头描述：" 等），避免标题出现"画面描述：xxx"。
    seg_title = (seg_data.get("title") or "").strip()
    if not seg_title and (segment.description or "").strip():
        import re as _re
        _desc = _re.sub(r"^(画面描述|镜头描述|分镜描述|描述)?[:：]?\s*", "", (segment.description or "").strip())
        seg_title = _desc.strip()[:4]
    segment.title = seg_title[:100] or None
    # 2026-08-10：动作序列标记（打斗/动作段连续分镜同一标记，如 as_1；非动作段为 None）
    segment.action_sequence = (seg_data.get("action_sequence") or "").strip() or None

    # dialogue 字符串字段兼容：由 dialogue_lines 拼接；无则回退 seg_data["dialogue"]
    if norm_lines:
        segment.dialogue = "\n".join(f"{l['speaker']}：{l['text']}" for l in norm_lines)
    else:
        segment.dialogue = seg_data.get("dialogue") or None


def generate_draft(
    db: Session,
    synopsis: str,
    *,
    model_id=None,
    episode_count: int | None = None,
    style_hint: str | None = None,
    per_duration: int = 15,
    max_retries: int = 3,
) -> dict:
    """调用 LLM 返回结构化草案 dict。

    含重试机制：LLM 偶发返回格式异常或网络抖动时自动重试（默认 3 次），
    每次重试间隔 2 秒。LLM 调用失败和 JSON 解析失败均会触发重试。

    style_hint：视觉风格提示（英文 prompt 片段），注入 LLM prompt 影响分镜描述措辞。
    per_duration：分镜时长上限（5/10/15s），LLM 按镜头内容在 1~上限内配置每镜时长。
    """
    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    hint = f"- 建议生成 {episode_count} 幕\n" if episode_count else ""
    if style_hint:
        hint += f"- 视觉风格：{style_hint}\n"
    prompt = _NEW_PROMPT_TMPL.format(
        synopsis=synopsis.strip(), episode_hint=hint, per_duration=per_duration
    )

    last_err: ValueError | None = None
    for attempt in range(1, max_retries + 1):
        # 1) 调 LLM
        try:
            resp = provider.chat([{"role": "user", "content": prompt}])
        except Exception as e:
            last_err = ValueError(f"剧本模型调用失败（第{attempt}/{max_retries}次）：{map_to_chinese(e)}")
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise last_err

        # 2) 解析 JSON
        try:
            content = resp["choices"][0]["message"]["content"]
            return _extract_json(content)
        except Exception as e:
            snippet = ""
            try:
                snippet = (resp["choices"][0]["message"]["content"] or "")[:300]
            except Exception:
                pass
            last_err = ValueError(
                f"剧本模型返回格式异常，无法解析为 JSON（第{attempt}/{max_retries}次）：{e}"
                f"；原始内容片段：{snippet!r}"
            )
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise last_err

    raise last_err  # 理论上不会到达


_EP_NUM_TOKENS = re.compile(r"第\s*\d+\s*[集幕]")


def normalize_episode_title(raw: str | None, index: int) -> str:
    """幕标题归一：统一为「第N幕 <幕名>」。

    - 去掉标题中所有「第N集 / 第N幕」序号字样，只保留幕名（「第X集」是剧本标记，幕里不应出现）
    - 「第1幕 第1集 夜店救人」→「第1幕 夜店救人」；「第3集 天台决战」→「第3幕 天台决战」
    - 幕名为空 → 回退「第N幕」空壳；系统占位「主幕」保持原样
    - index 为幕的 1 基序号（第 N 幕的 N），全角/半角空格统一整理
    """
    t = (raw or "").strip()
    name = re.sub(r"\s+", " ", _EP_NUM_TOKENS.sub(" ", t)).strip(" ·")
    if not name:
        return f"第{index}幕"
    if name == "主幕":
        return name
    return f"第{index}幕 {name}"[:200]


def materialize_draft(
    db: Session,
    draft: dict,
    *,
    synopsis: str,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    style_id=None,
    art_style_prompt: str | None = None,
    existing_project=None,
    per_duration: int | None = None,
    video_params: dict | None = None,
) -> Project:
    """把 LLM 草案落库：Project + Episodes + Assets + Segments（带资产关联）。

    资产为项目级（跟项目，不跨项目复用）。
    事务保护：全程在 try/except 内执行，任何中途异常（flush 失败、数据格式
    错误等）都显式 rollback，避免半成品项目残留。_find_or_create_asset 内部
    的 SAVEPOINT 异常已被自身捕获，不会触发此处的 rollback。

    aspect_ratio/style_id/art_style_prompt：项目创建时的屏幕尺寸与风格配置。

    per_duration：分镜时长上限（5/10/15s）。LLM 按镜头内容在 1~上限内配置每镜
    duration；落库时对每镜 duration 做硬约束收敛（1 ~ per_duration），防止 LLM
    偶发超上限导致视频生成失败。同时为每幕预写 video_script（兼容幕级视频产物）。

    P6 续接模式：existing_project 非 None 时不新建 Project，而是把草案的
    episodes/segments **追加**到该项目——assets 同名复用（_find_or_create_asset
    按已有项目查找），幕 index 从已有最大幕号 +1 续接；用于小说章节分批改编。
    """
    try:
        title = (draft.get("title") or "未命名短剧").strip()[:200] or "未命名短剧"
        script = draft.get("script")

        if existing_project is not None:
            # 续接模式：复用已有项目，不覆盖其 title/synopsis/style
            p = existing_project
        else:
            p = Project(
                title=title,
                synopsis=synopsis.strip(),
                script=script,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                style_id=style_id,
                art_style_prompt=art_style_prompt,
                video_params=video_params or {},
                status=ProjectStatus.draft,
            )
            db.add(p)
            db.flush()

        # 1) 落 assets：构建 (type, name) → asset_id 映射
        asset_map: dict[tuple[str, str], str] = {}
        # 续接模式：预填已有项目资产，确保续接分镜可引用旧角色/场景
        if existing_project is not None:
            for a in existing_project.assets:
                asset_map[(a.type.value, _norm_name(a.name))] = str(a.id)
        for a in draft.get("assets") or []:
            try:
                atype = AssetType(a.get("type"))
            except ValueError:
                continue
            name = (a.get("name") or "").strip()
            if not name:
                continue
            asset = _find_or_create_asset(
                db,
                name=name,
                type_=atype,
                description=a.get("description"),
                project_id_for_new=p.id,
            )
            asset_map[(atype.value, _norm_name(name))] = str(asset.id)

        # 2) 落 episodes + segments
        episodes_data = draft.get("episodes") or []
        if not episodes_data:
            # 兜底：旧格式（无 episodes，直接 segments）→ 单幕
            episodes_data = [{"title": "主幕", "synopsis": None, "segments": draft.get("segments") or []}]

        # 幕号基准：新建项目从 0 起；续接模式从已有最大幕号 +1 起
        if existing_project is not None:
            next_index = max((ep.index for ep in p.episodes), default=-1) + 1
        else:
            next_index = 0

        new_episodes: list[Episode] = []
        # P7.6：预写 video_script 时的项目有效风格片段（Art style 前缀）
        style_frag = ""
        if per_duration is not None:
            try:
                from app.services.style_service import get_effective_style_prompt
                style_frag = get_effective_style_prompt(db, p)
            except Exception:
                style_frag = ""
        for i, ep_data in enumerate(episodes_data):
            # 2026-08-09（方案B）：对白超限分镜自动拆分（含单句拆句），
            # 拆分后的连续分镜统一落库，video_script 亦基于拆分后分镜预写。
            ep_data = dict(ep_data)
            ep_data["segments"] = [
                piece
                for seg in (ep_data.get("segments") or [])
                for piece in _split_overlong_segments(seg)
            ]
            ep = Episode(
                project_id=p.id,
                index=next_index + i,
                title=normalize_episode_title(ep_data.get("title"), next_index + i + 1),
                synopsis=ep_data.get("synopsis"),
            )
            db.add(ep)
            db.flush()
            for j, seg_data in enumerate(ep_data["segments"]):
                # 2026-08-16：落库即校正分镜时长——LLM 按镜头内容自动配置（1~per_duration，上限默认 15s），
                # 此处收敛到 [5, per_duration]（模型时长硬约束）；对白超限拆分阈值随 duration 动态放宽。
                duration = calibrate_segment_duration(seg_data, per_duration)
                seg = Segment(
                    episode_id=ep.id,
                    index=j + 1,
                    shot_type=seg_data.get("shot_type"),
                    camera=seg_data.get("camera"),
                    description=_clean_segment_description(seg_data.get("description")),
                    dialogue=seg_data.get("dialogue"),
                    narration=seg_data.get("narration"),
                    duration=duration,
                    shot_beats=normalize_shot_beats(
                        seg_data.get("shot_beats"), duration,
                        seg_data.get("shot_type"), seg_data.get("camera"),
                    ),
                )
                db.add(seg)
                db.flush()
                _link_segment(seg, seg_data=seg_data, asset_map=asset_map)
            # P7.6：改编链路预写该幕 video_script（splits 单段 + 幕级叙事）
            if per_duration is not None:
                ep.video_script = _build_episode_script(
                    db, ep, ep_data, asset_map, per_duration, style_frag,
                )
            new_episodes.append(ep)
        db.commit()
        db.refresh(p)

        # 2026-08-19：项目落库后自动生成「故事绑定」美术风格锚点（style_id/art_style_prompt
        # 均无时才生成；生成失败静默回退，不影响项目创建）。解决现代都市剧主角/场景被
        # 自由发挥成道士/欧式风格的风格割裂问题。
        try:
            from app.services.style_service import ensure_project_art_style
            ensure_project_art_style(db, p)
        except Exception:  # noqa: BLE001
            pass

        return p
    except Exception:
        db.rollback()
        raise
