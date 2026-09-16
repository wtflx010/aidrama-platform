"""角色声线档案服务：LLM 推荐声线 + 解析 voice_profile 为 TTSOpts。

voice_profile 结构（存 Asset.voice_profile JSONB）：
{
  "gender": "male|female|neutral",
  "age_group": "child|youth|middle|elder",
  "timbre_tags": ["低沉","温和"...],
  "reference_audio_url": "<参考音频url，CosyVoice zero_shot 用>",
  "reference_audio_text": "<参考音频对应文本>",
  "default_emotion": "平静",
  "voice_description": "LLM 生成的声线描述"
}

旁白声线结构同上，存 Project.narrator_profile JSONB。
"""
import logging
import re

from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetType
from app.models.model_config import ModelType
from app.models.project import Project
from app.providers.base import TTSOpts
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model

logger = logging.getLogger(__name__)

# 默认旁白声线（项目未配置时兜底）：中性稳重男声
_DEFAULT_NARRATOR_PROFILE = {
    "gender": "male",
    "age_group": "middle",
    "timbre_tags": ["稳重", "中性", "低沉"],
    "reference_audio_url": None,  # None → 用 CosyVoice 服务端 default 注册声音
    "reference_audio_text": None,
    "default_emotion": "平静",
    "voice_description": "默认旁白：中性稳重男声",
}


# ─── voice_profile → TTSOpts 解析 ────────────────────────────────

def _resolve_voice_id(voice_profile: dict) -> str:
    """从 voice_profile 解析 voice_id。

    - 有 reference_audio_url → 返回 "default"（实际通过 prompt_wav 传参考音频）
    - 无 → 按性别+年龄段匹配预置声音（preset_male_youth 等）
    """
    from app.services.voice_preset_service import match_preset_voice

    vp = voice_profile or {}
    if vp.get("reference_audio_url"):
        return "default"
    return match_preset_voice(vp.get("gender"), vp.get("age_group"))


# 情绪标签 → CosyVoice instruct2 自然语言指令映射
# instruct2 仅支持情绪/语气控制，不支持声线描述
_EMOTION_INSTRUCT_MAP = {
    "平静": "用平稳自然的语气说",
    "愤怒": "用愤怒且有力的语气说",
    "悲伤": "用悲伤低沉的语气说",
    "欢快": "用欢快轻快的语气说",
    "紧张": "用紧张急促的语气说",
    "温馨": "用温柔温馨的语气说",
    "恐惧": "用恐惧颤抖的语气说",
    "史诗": "用庄重宏大的语气说",
    "冷漠": "用冷漠平淡的语气说",
    "震惊": "用震惊的语气说",
}


def _build_tts_opts(
    voice_profile: dict,
    emotion: str | None = None,
    instruct_text: str | None = None,
) -> TTSOpts:
    """从 voice_profile + 情绪构造 TTSOpts。

    - voice_id：固定 "default"（CosyVoice 服务端用）
    - prompt_wav/prompt_text：voice_profile 有参考音频则传，走一次性 zero_shot
    - instruct_text：只放情绪指令（CosyVoice instruct2 模式仅支持情绪/语气控制，
      不支持声线描述；声线由 prompt_wav 或 voice 字段决定）
    """
    vp = voice_profile or {}
    # instruct_text：优先调用方显式指定，其次 emotion 映射
    if not instruct_text and emotion:
        instruct_text = _EMOTION_INSTRUCT_MAP.get(emotion, f"用{emotion}的语气说")
    opts = TTSOpts(
        voice=_resolve_voice_id(vp),
        emotion=emotion,
        instruct_text=instruct_text,
    )
    ref_url = vp.get("reference_audio_url")
    if ref_url:
        opts.prompt_wav = ref_url
        opts.prompt_text = vp.get("reference_audio_text") or ""
    return opts


def get_character_voice_profile(db: Session, character_id) -> dict:
    """取角色声线档案，无则返回空 dict。"""
    asset = db.get(Asset, character_id)
    if not asset or asset.type != AssetType.character:
        return {}
    return asset.voice_profile or {}


def get_narrator_profile(db: Session, project_id) -> dict:
    """取项目旁白声线，无配置则返回默认。"""
    project = db.get(Project, project_id)
    if not project:
        return _DEFAULT_NARRATOR_PROFILE.copy()
    np = project.narrator_profile or {}
    if not np:
        return _DEFAULT_NARRATOR_PROFILE.copy()
    return np


def ensure_narrator_profile(db: Session, project) -> dict:
    """确保项目旁白音色已配置；未配置时由 LLM 按剧情生成并落库（全片一致）。

    2026-08-10：此前旁白无配置一律回退「中性稳重男声」——若男主也是男声，
    旁白与男主同声（用户反馈听感异常）。改为由 LLM 依据项目剧情（标题/梗概/
    剧本片段）判断旁白应采用的性别、年龄段与气质音色，生成一次写库，
    之后全片旁白保持该音色一致。LLM 失败时回退默认男声（不阻断）。
    """
    if project is None:
        return _DEFAULT_NARRATOR_PROFILE.copy()
    if project.narrator_profile:
        return project.narrator_profile
    try:
        profile = _generate_narrator_profile_with_llm(db, project)
        if profile:
            project.narrator_profile = profile
            db.commit()
            logger.info("[voice] 已由 LLM 按剧情生成旁白音色 project=%s: %s",
                        project.id, profile.get("voice_description"))
            return profile
    except Exception as e:
        logger.warning("[voice] 旁白音色 LLM 生成失败，回退默认 project=%s: %s",
                       project.id, e)
        db.rollback()
    return _DEFAULT_NARRATOR_PROFILE.copy()


def _generate_narrator_profile_with_llm(db: Session, project: Project) -> dict:
    """调 LLM 依据项目剧情生成旁白音色档案（失败抛异常）。"""
    from app.services.prompt_enhance_service import _extract_json

    model = _resolve_model(db, None, ModelType.text, "script")
    provider = ProviderRegistry.for_model(model)
    snippet = (project.script or "").strip()[:600] or (project.synopsis or "").strip()[:300]
    # 注入项目全部角色及其声线，供旁白音色差异化避开（2026-08-18：此前仅泛泛要求
    # "与角色区分"，LLM 仍可能给出与女主同型女声 → 用户听感"旁白像妈妈在说"）
    char_lines = []
    if project is not None:
        from app.models.asset import Asset, AssetType

        chars = db.query(Asset).filter(
            Asset.project_id == project.id,
            Asset.type == AssetType.character,
        ).all() if project else []
        for a in chars:
            vp = a.voice_profile or {}
            desc = vp.get("voice_description") or ""
            char_lines.append(
                f"- {a.name}（{vp.get('gender') or '?'}/{vp.get('age_group') or '?'}：{desc or '未配置'}）"
            )
    char_block = "\n".join(char_lines) or "（无角色声线信息，请按剧情设定）"
    prompt = (
        "你是资深有声剧配音导演。请根据下面的项目剧情，为该剧的「旁白/画外音叙述者」"
        "选定一个合适的音色：性别、年龄段、音色气质，以及旁白的整体情感基调。\n"
        f"【项目标题】{project.title or '未知'}\n"
        f"【剧情梗概/剧本片段】{snippet or '（无，请按通用叙事基调判断）'}\n"
        f"【主要角色及其配音声线】\n{char_block}\n\n"
        "要求：\n"
        f"1. 【重点】旁白音色必须与上方列出的每一条角色声线都能明显区分——尤其当"
        "旁白候选性别/年龄段与某角色相同时，必须在音色气质上拉开明显差距"
        "（例如角色是清亮尖锐的女性，旁白应选圆润低沉/知性磁性的女声；"
        "角色是温和中年男声，旁白可选清亮女声或苍老低音，绝不同声）；\n"
        "2. 与剧情题材和氛围匹配（如仙侠苍凉→清冷/苍劲、热血燃向→沉稳厚重、"
        "悬疑惊悚→低沉幽深、都市情感→温润平和）；\n"
        "3. 全片统一，只输出一个旁白音色，不要随分镜变化；\n"
        '只输出 JSON（禁止多余文字）：{"gender": "male|female|neutral", '
        '"age_group": "child|youth|middle|elder", '
        '"timbre_tags": ["如：沉稳","低沉","清冷"], '
        '"voice_description": "旁白：一句完整的中文音色描述（含性别与气质，并注明与哪位角色声线区分）", '
        '"emotion": "旁白整体情感基调（中文）"}'
    )
    resp = provider.chat([
        {"role": "system", "content": "你是专业的配音导演，只输出符合要求的 JSON。"},
        {"role": "user", "content": prompt},
    ])
    content = resp["choices"][0]["message"]["content"]
    data = _extract_json(content) or {}
    gender = data.get("gender")
    if gender not in ("male", "female", "neutral"):
        gender = "neutral"
    age = data.get("age_group")
    if age not in ("child", "youth", "middle", "elder"):
        age = "middle"
    tags = data.get("timbre_tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = [str(t).strip() for t in tags if str(t).strip()][:5]
    desc = str(data.get("voice_description") or "").strip()
    if not desc:
        desc = "旁白：" + "、".join(tags) or "旁白：中性叙述声"
    return {
        "gender": gender,
        "age_group": age,
        "timbre_tags": tags,
        "reference_audio_url": None,
        "reference_audio_text": None,
        "default_emotion": str(data.get("emotion") or "平静"),
        "voice_description": desc,
    }


def voice_profile_description(profile: dict | None) -> str:
    """voice_profile → 音色中文描述（角色声线/旁白声线通用）。

    2026-08-09：视频模型原生语音在提示词未指定音色时随机发声，须把声线描述
    注入提示词才能固定一致。示例：默认「中性稳重男声」→ "中性稳重的中年男声"；
    profile 含 voice_description 时优先使用其描述。

    2026-08-09（性别硬约束修复）：LLM 转写英文音色描述时性别词不稳定——
    实测同一「少年音色」档案，有的镜头转写成 `youthful male voice`（男声），
    有的只写成 `clear ethereal boy voice`（无 male）→ 模型按清亮嗓音输出女声。
    因此当 gender=male/female 且描述缺失对应性别字（男/女）时，强制前缀
    「男声·/女声·」，保证六段式链路 LLM 转写必含显式性别。
    """
    p = profile or {}
    gender = {
        "male": "男声", "female": "女声", "neutral": "中性声音",
    }.get((p.get("gender") or "neutral"), "中性声音")
    age = {
        "child": "少年", "youth": "青年", "middle": "中年", "elder": "老年",
    }.get((p.get("age_group") or ""), "")
    tags = "、".join(p.get("timbre_tags") or [])
    if p.get("voice_description"):
        # 有完整声线描述（如「默认旁白：中性稳重男声」）→ 去掉「旁白：」前缀直接用作音色
        desc = str(p["voice_description"]).strip()
        desc = re.sub(r"^.*?[：:]\s*", "", desc) if re.search(r"[：:]", desc) else desc
        # 性别硬约束：male/female 且描述不含对应性别字 → 前缀补全
        if gender in ("男声", "女声") and gender[0] not in desc:
            desc = f"{gender}·{desc}"
        return desc
    parts = [t for t in (tags, age, gender) if t]
    return "、".join(parts) if parts else "中性稳重男声"


def narrator_voice_description(profile: dict | None) -> str:
    """旁白音色中文描述（复用通用实现，语义别名）。"""
    return voice_profile_description(profile)


def build_speaker_voice_map(db: Session, character_ids: list) -> dict[str, str]:
    """角色声线映射：角色名 → 音色描述（仅已定义声线档案的角色）。

    2026-08-09：生视频时角色声音须严格按声线档案执行；**未定义档案的角色
    不注入声线约束，模型可自由发挥**。空档案/无档案角色不在返回结果中。
    """
    import uuid as _uuid

    result: dict[str, str] = {}
    for cid in character_ids or []:
        try:
            a = db.get(Asset, _uuid.UUID(str(cid)))
        except (ValueError, TypeError):
            continue
        if not a or a.type != AssetType.character:
            continue
        vp = a.voice_profile or {}
        if not (
            vp.get("gender") or vp.get("age_group")
            or vp.get("timbre_tags") or vp.get("voice_description")
        ):
            continue  # 未定义档案 → 模型自由发挥，不注入声线约束
        desc = voice_profile_description(vp)
        if desc:
            result[a.name] = desc
            # 2026-08-09（M1）：对白 speaker 是 LLM 自由生成的名字，可能与资产名不一致
            # （昵称/加后缀等）→ 同时注册 character_id 键，调用方优先按 character_id 匹配
            result[str(a.id)] = desc
    return result


def set_narrator_profile(db: Session, project_id, profile: dict) -> Project:
    """设置项目旁白声线。"""
    project = db.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")
    project.narrator_profile = profile
    db.commit()
    db.refresh(project)
    return project


# ─── 预设声线回退 + 一致性校验 ───────────────────────────────────

# 性别/年龄关键词（用于预设回退与一致性校验）
_FEMALE_KEYWORDS = ("女", "娘", "姐", "妹", "姑", "婆", "妇", "姬", "妃", "后")
_MALE_KEYWORDS = ("男", "兄", "弟", "翁", "叟", "伯", "公", "汉", "郎", "王")
_CHILD_KEYWORDS = ("幼", "童", "孩", "小", "少年", "少女", "孩童", "小孩")
_ELDER_KEYWORDS = ("老", "叟", "翁", "伯", "公", "长辈", "长者")


def _guess_gender(description: str) -> str:
    """根据描述关键词猜测性别。"""
    desc = description or ""
    if any(k in desc for k in _FEMALE_KEYWORDS):
        return "female"
    if any(k in desc for k in _MALE_KEYWORDS):
        return "male"
    return "neutral"


def _guess_age_group(description: str) -> str:
    """根据描述关键词猜测年龄段。"""
    desc = description or ""
    if any(k in desc for k in _CHILD_KEYWORDS):
        return "child"
    if any(k in desc for k in _ELDER_KEYWORDS):
        return "elder"
    return "youth"


def fallback_profile_by_description(name: str, description: str | None) -> dict:
    """无 LLM 可用时，基于角色描述关键词生成预设声线档案。

    用于 voice_profile 为空且 recommend_voice_profile 失败的兜底场景，
    确保不同角色至少有性别/年龄差异，不至于全部用 default 声音。
    """
    desc = description or name or ""
    gender = _guess_gender(desc)
    age = _guess_age_group(desc)

    # 基础音色标签（按性别 + 年龄组合）
    tags: list[str] = []
    if gender == "female":
        tags.extend(["清亮", "柔和"])
        if age == "child":
            tags = ["稚嫩", "清脆", "活泼"]
        elif age == "elder":
            tags = ["苍老", "缓慢", "慈祥"]
    elif gender == "male":
        tags.extend(["低沉", "沉稳"])
        if age == "child":
            tags = ["稚嫩", "清脆", "活泼"]
        elif age == "elder":
            tags = ["沙哑", "苍老", "沉稳"]
    else:
        tags = ["中性", "平稳"]

    return {
        "gender": gender,
        "age_group": age,
        "timbre_tags": tags,
        "reference_audio_url": None,
        "reference_audio_text": None,
        "default_emotion": "平静",
        "voice_description": f"预设声线（基于描述推断）：{gender}/{age}",
    }


def check_voice_visual_consistency(asset: Asset, profile: dict) -> list[str]:
    """校验声线档案与角色视觉描述是否一致，返回警告列表（不阻断）。

    校验项：
    - profile.gender 与 asset.description 关键词性别是否一致
    - profile.age_group 与 asset.description 关键词年龄是否一致
    """
    warnings: list[str] = []
    if not profile or not asset:
        return warnings
    desc = (asset.description or "") + " " + (asset.expanded_description or "")
    if not desc.strip():
        return warnings

    profile_gender = (profile.get("gender") or "neutral").lower()
    profile_age = (profile.get("age_group") or "").lower()

    # 性别校验
    if profile_gender != "neutral":
        desc_gender = _guess_gender(desc)
        if desc_gender != "neutral" and desc_gender != profile_gender:
            warnings.append(
                f"声线性别({profile_gender})与角色描述({desc_gender})不一致，"
                f"可能导致声音与人物形象不匹配"
            )

    # 年龄校验
    if profile_age:
        desc_age = _guess_age_group(desc)
        if desc_age != "youth" and desc_age != profile_age:
            warnings.append(
                f"声线年龄段({profile_age})与角色描述({desc_age})不一致"
            )

    return warnings


# ─── LLM 推荐声线档案 ────────────────────────────────────────────

_RECOMMEND_PROMPT = """你是一名配音导演。根据角色设定，推荐合适的声线档案。

要求返回**纯 JSON**（不要 markdown 代码块、不要解释），结构如下：
{{
  "gender": "male|female|neutral",
  "age_group": "child|youth|middle|elder",
  "timbre_tags": ["3-5个音色标签，如低沉/温和/沙哑/清亮/磁性"],
  "default_emotion": "该角色默认情绪：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊",
  "voice_description": "一句话描述该角色声线特点，便于配音参考"
}}

约束：
- gender/age_group 须与角色设定中的性别年龄匹配
- timbre_tags 须符合角色性格（如反派→沙哑/阴沉，主角→清亮/坚定）
- default_emotion 须符合角色常态情绪
- reference_audio_url/reference_audio_text 不输出（由用户后续上传/指定）
- 只返回 JSON

角色名：{name}
角色描述：{description}
角色扩写描述：{expanded_description}"""


def _extract_json(text: str) -> dict:
    """从 LLM 输出提取 JSON（复用 llm_script_service 的容错逻辑简化版）。"""
    import json
    import re
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError(f"LLM 输出不含 JSON 对象：{s[:200]!r}")
    s = s[i:j + 1]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return json.loads(s, strict=False)


def recommend_voice_profile(
    db: Session,
    asset_id,
    model_id=None,
) -> dict:
    """LLM 根据角色设定推荐声线档案。

    输入：Asset.name + description + expanded_description
    输出：voice_profile dict（不含 reference_audio_url/text，由用户后续指定）
    """
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    if asset.type != AssetType.character:
        raise ValueError("仅角色资产支持声线推荐")

    description = asset.description or asset.name
    expanded = asset.expanded_description or "（无扩写描述）"

    model = _resolve_model(db, model_id, ModelType.text, "voice_recommend")
    provider = ProviderRegistry.for_model(model)
    prompt = _RECOMMEND_PROMPT.format(
        name=asset.name,
        description=description,
        expanded_description=expanded,
    )
    try:
        resp = provider.chat([{"role": "user", "content": prompt}])
    except Exception as e:
        raise ValueError(map_to_chinese(e))

    content = resp["choices"][0]["message"]["content"]
    profile = _extract_json(content)
    # 补齐字段 + 不信任 LLM 输出的 reference_audio_url（安全：强制清空，由用户指定）
    profile.setdefault("gender", "neutral")
    profile.setdefault("age_group", "youth")
    profile.setdefault("timbre_tags", [])
    profile.setdefault("default_emotion", "平静")
    profile.setdefault("voice_description", "")
    profile["reference_audio_url"] = None
    profile["reference_audio_text"] = None
    return profile


def apply_voice_profile(db: Session, asset_id, profile: dict) -> tuple[Asset, list[str]]:
    """写回 asset.voice_profile，返回 (asset, warnings)。

    warnings 为声线-视觉一致性校验结果（不阻断写入）。
    """
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    if asset.type != AssetType.character:
        raise ValueError("仅角色资产支持声线档案")
    asset.voice_profile = profile
    db.commit()
    db.refresh(asset)
    warnings = check_voice_visual_consistency(asset, profile)
    if warnings:
        logging.warning("角色 %r 声线一致性警告：%s", asset.name, warnings)
    return asset, warnings


def update_voice_profile(db: Session, asset_id, partial: dict) -> tuple[Asset, list[str]]:
    """部分更新 asset.voice_profile（合并，非覆盖），返回 (asset, warnings)。"""
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ValueError("资产不存在")
    if asset.type != AssetType.character:
        raise ValueError("仅角色资产支持声线档案")
    merged = dict(asset.voice_profile or {})
    merged.update(partial)
    asset.voice_profile = merged
    db.commit()
    db.refresh(asset)
    warnings = check_voice_visual_consistency(asset, merged)
    if warnings:
        logging.warning("角色 %r 声线一致性警告：%s", asset.name, warnings)
    return asset, warnings
