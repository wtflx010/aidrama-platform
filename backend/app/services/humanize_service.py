"""去AI味审校 pass：生成文本交付前的自动检测 + 定向改写。

流程（两段式，最小成本）：
1. 检测：LLM 按黑名单（writing_style.AI_MARKER_TERMS / DEAI_RULES_TEXT）圈出疑似 AI 味句子
   （要求逐字复制原句）。没有命中且档位=auto → 原样返回，零改动。
2. 改写：LLM 把命中句子逐句改写为人类感更强的表达，只替换命中句，
   其余文本逐字保留 → 结构/标记行/对白主体不被扰动。

档位（level）：
- off：跳过（返回原文）
- auto（默认）：仅当检测到疑似句才触发改写
- strict：始终检测 + 改写所有命中句

稳健性：任何异常都不让写作任务失败——捕获后原样返回原文并记 warning。
"""

import json
import logging
import re

from sqlalchemy.orm import Session

from app.models.model_config import ModelType
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model
from app.services import writing_style

logger = logging.getLogger(__name__)

_DETECT_SYSTEM = """你是短剧/小说审稿编辑。检查下方文本中明显符合「AI 味特征」的句子，把它们挑出来。

AI 味特征清单（只挑明显命中的，拿不准的不标，宁可漏标不误伤）：
1. 模板化起句：时空感叹、万能开头（如「在这个喧嚣的世界里」「时间仿佛凝固」）。
2. 三连排比或「越…越…」句群、格式对仗句两两出现。
3. 情绪汇报：直接贴情绪标签（如「眼中闪过一丝复杂的情绪」「眸色一沉」「嘴角勾起一抹冷笑」）。
4. 空泛升华：结尾强行点题、金句总结（如「生活就像…」「或许这就是…的意义」）。
5. 书面腔：对白或旁白使用「仿佛/宛如/氤氲/凝视/决然」等书面词。
6. 万能反应模板：如「她愣住了」「他沉默了良久」「空气安静得可怕」。
7. 对白书面化/无个性/话说太满（每句都解释清楚、无留白、无个性口吻）。

{style_hint}

输出要求：只输出严格 JSON，不要任何其它文字：
{"flagged": [{"text": "原句（逐字复制，含标点），必须与原文一字不差", "reason": "命中哪类特征（简短）"}]}
若没有命中输出 {"flagged": []}
"""

_REWRITE_SYSTEM = """你是人类风格的小说/短剧写手。把下列「AI 味句子」改写成人类感更强的表达。

改写要求：
1. show, don't tell：状态用动作/物件/细节呈现，不直接贴情绪。
2. 留白克制：不点破情绪，绝对不做金句升华。
3. 对白：短句、口语、带停顿，保留原角色的口气；话不说满。
4. 改为书面腔的句子要口语化。
5. 与文风档位保持一致。

{style_hint}

每条改写：保留原意、上下文节奏与长度量级，不要新增剧情信息，不要解释。

输出要求：只输出严格 JSON，不要任何其它文字：
{"rewrites": [{"original": "原句（与输入逐字一致）", "rewritten": "改写后的句子"}]}
"""


def _split_units(text):
    """以句末标点（。！？!?）或换行为界，把文本切成可独立替换的单元。

    保留原文拼接时的分隔符信息：单元本身含结尾标点或换行，可直接拼接还原。
    """
    units = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？!?\n" or ch in "．":
            units.append(buf)
            buf = ""
    if buf:
        units.append(buf)
    return units


def _extract_json(raw):
    """容错解析 LLM 返回的 JSON（兼容 markdown fence / 前后多余文字）。"""
    text = (raw or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _call_provider(db, system, user, model_id, max_tokens=6000):
    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)
    resp = provider.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        suppress_thinking=True,
    )
    return ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def detect_ai_flavor(db, text, style_mode=None, model_id=None, max_items=40):
    """检测文本中疑似 AI 味句子。返回 [(original, reason), ...]，逐字命中可匹配。"""
    if not text or not text.strip():
        return []
    units = _split_units(text)
    body = "\n".join(f"{i + 1}. {u}" for i, u in enumerate(units))
    hint = writing_style.humanize_style_hint(style_mode)
    # 过长则截断正文（防御：超长输入只检测前 2 万字符）
    body = body[:20000]
    raw = _call_provider(db, _DETECT_SYSTEM.format(style_hint=hint), body, model_id)
    data = _extract_json(raw)
    flagged = data.get("flagged") or []
    issues = []
    if not isinstance(flagged, list):
        return []
    for item in flagged[:max_items]:
        if not isinstance(item, dict):
            continue
        original = (item.get("text") or "").strip()
        reason = (item.get("reason") or "").strip()
        # 只保留能逐字命中原文的句子（模型可能轻微改动，命不中则跳过，安全）
        if original and original in text:
            issues.append((original, reason))
    return issues


def rewrite_ai_flavor(db, text, issues, style_mode=None, model_id=None):
    """改写命中句子，只替换命中单元，其余逐字保留。返回新文本与改写对。"""
    if not issues:
        return text, []
    payload = {
        "flagged": [{"text": o, "reason": r} for o, r in issues[:40]],
    }
    hint = writing_style.humanize_style_hint(style_mode)
    raw = _call_provider(
        db, _REWRITE_SYSTEM.format(style_hint=hint),
        "\n\n".join(json.dumps(payload, ensure_ascii=False)),
        model_id,
    )
    data = _extract_json(raw)
    rewrites = data.get("rewrites") or []
    if not isinstance(rewrites, list):
        return text, []
    pairs = []
    new_text = text
    for item in rewrites[:60]:
        if not isinstance(item, dict):
            continue
        original = (item.get("original") or "").strip()
        rewritten = (item.get("rewritten") or "").strip()
        if not original or not rewritten or original == rewritten:
            continue
        if original not in new_text:
            continue
        new_text = new_text.replace(original, rewritten, 1)
        pairs.append((original, rewritten))
    return new_text, pairs


def humanize_text(
    db: Session, text: str,
    style_mode=None, level: str = "auto", model_id=None,
):
    """对外入口：文本去 AI 味。返回 (new_text, meta)。

    meta: {"level", "detected", "rewritten", "error"}。任何异常原样返回原文。
    """
    meta = {"level": level, "detected": 0, "rewritten": 0, "error": None}
    if not text or not text.strip() or level == "off":
        return text, meta
    try:
        issues = detect_ai_flavor(db, text, style_mode=style_mode, model_id=model_id)
        meta["detected"] = len(issues)
        if not issues:
            return text, meta
        new_text, pairs = rewrite_ai_flavor(
            db, text, issues, style_mode=style_mode, model_id=model_id
        )
        meta["rewritten"] = len(pairs)
        if not pairs:
            return text, meta
        return new_text, meta
    except Exception as e:  # noqa: BLE001 —— 审校失败绝不阻断写作任务
        logger.warning("去AI味审校跳过（不阻断生成）: %s", e)
        meta["error"] = str(e)
        return text, meta
