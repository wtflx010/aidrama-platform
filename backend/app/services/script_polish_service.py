"""剧本文本 / 分镜润色服务（2026-08-22）：

- polish_script：整体润色剧本正文（保持【第X集】结构与角色/剧情不变，提升
  对白表现力、画面感、节奏），更新 novel.raw_text 及章节数/字数
- polish_segment：单分镜润色（描述更有画面感、对白更口语化、情绪更到位），
  输出结构化 JSON 更新分镜的 description / narration / dialogue_lines
"""
import json
import re
import time

from sqlalchemy.orm import Session

from app.models.model_config import ModelType
from app.models.novel import Novel
from app.models.segment import Segment
from app.providers.registry import ProviderRegistry
from app.services.keyframe_service import _resolve_model


def _extract_json(raw: str) -> dict:
    """提取 LLM 输出中的 JSON（兼容 markdown fence / 尾随逗号）。"""
    from app.services.llm_script_service import _extract_json as _e

    text = (raw or "").strip()
    try:
        return _e(text)
    except Exception:
        m = re.search(r"\{[\s\S]*?\}", text)
        if m:
            try:
                return _e(m.group(0))
            except Exception:
                pass
        return {}


# ── 整体润色 ──────────────────────────────────────────

SCRIPT_POLISH_PROMPT = """你是一名资深短剧编剧。请对下面的短剧剧本做**整体润色**，保持原剧本的人物、剧情、分幕（【第X集】标记）完全不变，只提升语言质量。

【润色要求】
1. 必须保留全部【第X集 标题】标记行、场次（内景/外景·地点·时间）、全部角色对白与旁白——不得删除、合并、新增场面或改写情节
2. 对白更口语化、自然、有张力（单句 ≤30 字），保留角色语气与性格
3. 动作/表演描述更有画面感、更具体（表情、动作细节），但不超过一句话
4. 旁白更精炼、有文学感
5. 修正常见语病、重复用词、生硬翻译腔
6. **只输出润色后的完整剧本正文本身**，不要任何解释、不要 markdown 代码块

【原文剧本】
{text}"""


def polish_script(db: Session, novel_id: str, model_id=None, max_retries: int = 2) -> dict:
    """整体润色剧本正文并落库。返回 {raw_text, word_count, chapters_count}。"""
    from app.services.novel_analysis_service import split_chapters

    novel = db.get(Novel, novel_id)
    if not novel:
        raise ValueError("剧本不存在")
    text = (novel.raw_text or "").strip()
    if not text:
        raise ValueError("剧本内容为空，无法润色")

    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)

    # 超长剧本截断保护（整体润色一次 LLM 调用，控制上下文）
    src = text if len(text) <= 20000 else text[:20000] + "\n\n……（后续内容略，本次润色仅覆盖以上范围）"

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat(
                [
                    {"role": "system", "content": SCRIPT_POLISH_PROMPT.format(text=src)},
                    {"role": "user", "content": "请润色上面的剧本。"},
                ],
                max_tokens=24000,
                suppress_thinking=True,
            )
            out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            out = (out or "").strip()
            if len(out) < max(20, len(src) // 2):
                raise ValueError(f"润色输出异常（过短 {len(out)} 字），请重试")
            if src[-20:].startswith("（") and "……" in src[-40:]:
                out = out  # 截断场景由用户自决
            novel.raw_text = out
            novel.chapters_count = len(split_chapters(out))
            novel.word_count = len(out)
            db.commit()
            return {"raw_text": out, "word_count": novel.word_count, "chapters_count": novel.chapters_count}
        except Exception as e:  # noqa: BLE001
            db.rollback()
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise
    assert last_err is not None
    raise last_err


# ── 单分镜润色 ────────────────────────────────────────

SEGMENT_POLISH_PROMPT = """你是一名资深短剧导演。请对单个分镜做**精细化润色**，只输出指定 JSON（不要 markdown 代码块）。

输入分镜信息：
- 景别：{shot_type}
- 机位：{camera}
- 画面描述：{description}
- 旁白：{narration}
- 对白：{dialogue}
- 情绪：{emotion}

要求输出：
{{
  "description": "润色后的画面描述：更有画面感、具体到动作/表情/机位效果，2~4 句，简体中文",
  "narration": "润色后的旁白：精炼有文学感（原文无旁白则输出空字符串）",
  "dialogue_lines": [
    {{"kind": "dialogue", "speaker": "角色名", "text": "润色后的对白（更口语化、有张力，单句≤30字）", "emotion": "情绪"}}
  ]
}}

约束：
- 不得改变镜头内容（谁、在哪、发生什么、镜头拍什么）与对白大意，只提升表达
- dialogue_lines 保持原说话人与条数；原无对白则是空数组 []
- emotion 取：愤怒|悲伤|平静|欢快|紧张|温馨|恐惧|史诗|冷漠|震惊
- 只返回 JSON"""


def polish_segment(db: Session, segment_id: str, model_id=None, max_retries: int = 2) -> dict:
    """单分镜润色并落库。返回更新后的字段 dict（可直出 SegmentOut 所需）。"""
    seg = db.get(Segment, segment_id)
    if not seg:
        raise ValueError("分镜不存在")

    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)

    dialogue_lines = seg.dialogue_lines or []
    dialogue_text = " / ".join(
        f"{d.get('speaker') or ''}：{d.get('text') or ''}" for d in dialogue_lines
    ) or (seg.dialogue or "")

    prompt = SEGMENT_POLISH_PROMPT.format(
        shot_type=seg.shot_type or "未知",
        camera=seg.camera or "未知",
        description=seg.description or "（无）",
        narration=seg.narration or "（无）",
        dialogue=dialogue_text or "（无）",
        emotion=seg.emotion or "平静",
    )

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = provider.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=6000,
                suppress_thinking=True,
            )
            raw = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            data = _extract_json(raw)
            if not data.get("description"):
                raise ValueError("润色结果缺少 description，请重试")
            new_lines = data.get("dialogue_lines") or []
            if isinstance(new_lines, list):
                cleaned = []
                for dl in new_lines[:20]:
                    if not isinstance(dl, dict):
                        continue
                    if str(dl.get("speaker") or "").strip() and str(dl.get("text") or "").strip():
                        cleaned.append({
                            "kind": str(dl.get("kind") or "dialogue"),
                            "speaker": str(dl.get("speaker") or "").strip(),
                            "text": str(dl.get("text") or "").strip(),
                            "emotion": str(dl.get("emotion") or seg.emotion or "平静"),
                        })
                new_lines = cleaned
            seg.description = str(data.get("description") or seg.description).strip()
            seg.narration = str(data.get("narration") or "").strip() or (seg.narration if seg.narration else None)
            seg.dialogue_lines = new_lines if new_lines else seg.dialogue_lines
            seg.dialogue = " / ".join(f"{d['speaker']}：{d['text']}" for d in new_lines) if new_lines else seg.dialogue
            db.commit()
            return {
                "id": str(seg.id),
                "description": seg.description,
                "narration": seg.narration,
                "dialogue": seg.dialogue,
                "dialogue_lines": seg.dialogue_lines,
            }
        except Exception as e:  # noqa: BLE001
            db.rollback()
            last_err = e
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise
    assert last_err is not None
    raise last_err
