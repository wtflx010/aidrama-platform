"""多语言配音与字幕导出（P2-5）。
"""
import json
import logging
import os
import uuid

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_LANG_NAMES = {
    "eng": "English", "spa": "Español", "fra": "Français", "deu": "Deutsch",
    "ita": "Italiano", "por": "Português", "rus": "Русский", "jpn": "日本語",
    "kor": "한국어", "vie": "Tiếng Việt", "tha": "ไทย", "ara": "العربية",
    "hin": "हिन्दी", "ind": "Bahasa Indonesia", "msa": "Bahasa Melayu",
    "chi": "中文(简体)", "yue": "粤語", "zho": "中文",
}


def lang_display(lang):
    norm = (lang or "").lower()
    if lang in _LANG_NAMES:
        return _LANG_NAMES[lang]
    if norm in _LANG_NAMES:
        return _LANG_NAMES[norm]
    return lang


def _chat(db, messages):
    from app.services.prompt_enhance_service import _resolve_text_model
    from app.providers.registry import ProviderRegistry

    model = _resolve_text_model(db, None, "script")
    provider = ProviderRegistry.for_model(model)
    resp = provider.chat(messages)
    return resp["choices"][0]["message"]["content"]


def translate(db, text, target_lang):
    text = (text or "").strip()
    if not text:
        return text
    lang_name = lang_display(target_lang)
    prompt = (
        "把下面这段微短剧对白/旁白/字幕翻译成" + lang_name + "。"
        "保持口吻自然口语化、贴合人物情绪，保留原意与语气词。只输出译文，不要任何解释或引号。"
        "\n\n原文：\n" + text
    )
    out = _chat(db, [
        {"role": "system", "content": "你是专业的影视字幕翻译，输出自然、地道的目标语言。"},
        {"role": "user", "content": prompt},
    ]).strip()
    return out


def _no_newline(s):
    return (s or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace('"', "'")


def _translate_segment(db, segment, target_lang):
    lang_name = lang_display(target_lang)
    dialogue_lines = (segment.dialogue_lines or []) or []
    lines_payload = [
        {"i": i, "speaker": (d.get("speaker") or "") or "", "text": (d.get("text") or "")}
        for i, d in enumerate(dialogue_lines) if isinstance(d, dict) and d.get("text")
    ]
    subs = [(sub.start_ms, sub.end_ms, sub.text or "") for sub in (getattr(segment, "subtitles", None) or [])]
    narration = (segment.narration or "").strip()
    if not lines_payload and not subs and not narration:
        return {"dialogue": [], "narration": None, "subtitles": []}

    parts = ["把下面微短剧分镜的文本翻译成" + lang_name + "，保持人物口吻与情绪。"]
    if lines_payload:
        parts.append("【对白】\n" + "\n".join(
            str(p["i"]) + ". " + p["speaker"] + "：" + _no_newline(p["text"]) for p in lines_payload))
    if narration:
        parts.append("【旁白】\n" + _no_newline(narration))
    if subs:
        parts.append("【字幕（按顺序）】\n" + "\n".join(
            str(i) + ". " + _no_newline(t) for i, (_, _, t) in enumerate(subs)))
    parts.append(
        "只输出以下结构的 JSON（键固定）："
        '{"dialogue": [{"i": 0, "text": "译文"}], "narration": "译文或空", "subtitles": [{"i": 0, "text": "译文"}]}'
        "。对白/字幕的 i 必须与上面的编号一致，条数完全对应，不得增删。只输出 JSON，不要解释。")
    prompt = "\n".join(parts)

    try:
        content = _chat(db, [
            {"role": "system", "content": "你是影视翻译，只输出符合要求的 JSON。"},
            {"role": "user", "content": prompt},
        ])
        data = _extract_json(content)
    except Exception:
        data = {
            "dialogue": [{"i": p["i"], "text": translate(db, p["text"], target_lang)} for p in lines_payload],
            "narration": translate(db, narration, target_lang) if narration else "",
            "subtitles": [{"i": i, "text": translate(db, t, target_lang)} for i, (_, _, t) in enumerate(subs)],
        }

    t_lines = {int(d.get("i")): (d.get("text") or "") for d in data.get("dialogue", []) if isinstance(d, dict)}
    t_subs = {int(s.get("i")): (s.get("text") or "") for s in data.get("subtitles", []) if isinstance(s, dict)}
    t_narration = (data.get("narration") or "").strip() or None

    return {
        "dialogue": [
            {"speaker": p["speaker"], "text": t_lines.get(p["i"], p["text"]) or p["text"],
             "emotion": (dialogue_lines[p["i"]].get("emotion") if p["i"] < len(dialogue_lines) else None),
             "character_id": (str(dialogue_lines[p["i"]].get("character_id")) if p["i"] < len(dialogue_lines) and dialogue_lines[p["i"]].get("character_id") else None)}
            for p in lines_payload
        ],
        "narration": t_narration or (translate(db, narration, target_lang) if narration else None),
        "subtitles": [
            {"start_ms": sm, "end_ms": em, "text": t_subs.get(i, t) or t}
            for i, (sm, em, t) in enumerate(subs)
        ],
    }


def _extract_json(text):
    s = (text or "").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("LLM 未返回 JSON")
    return json.loads(s[i:j + 1])


def translate_episode(db, episode, target_lang):
    segs = sorted(episode.segments, key=lambda s: s.index)
    result = {"lang": target_lang, "lang_name": lang_display(target_lang), "segments": []}
    for s in segs:
        tr = _translate_segment(db, s, target_lang)
        result["segments"].append({
            "segment_index": s.index, "segment_id": str(s.id),
            "dialogue": tr["dialogue"], "narration": tr["narration"], "subtitles": tr["subtitles"],
        })
    return result


def render_srt(subtitles):
    def _ts(ms):
        ms = max(0, int(ms))
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for i, sub in enumerate(subtitles, 1):
        lines.append(str(i))
        lines.append(_ts(sub["start_ms"]) + " --> " + _ts(sub["end_ms"]))
        lines.append(sub["text"])
        lines.append("")
    return "\n".join(lines)


def save_srt(episode, translated):
    from app.config import settings

    subs = []
    flag = False
    for seg in translated["segments"]:
        if seg["subtitles"]:
            flag = True
        subs.extend(seg["subtitles"])
    if not flag:
        for seg in translated["segments"]:
            for d in seg["dialogue"]:
                t = d["text"]
                dur = max(1000, int(len(t) / 4 * 1000))
                subs.append({"start_ms": 0, "end_ms": dur, "text": t})
    lang = translated.get("lang") or "zh-CN"
    srt = render_srt(subs)
    ext = lang.lower().replace("-", "_")
    ep_dir = os.path.join(settings.media_dir, "multilingual", str(episode.id))
    os.makedirs(ep_dir, exist_ok=True)
    path = os.path.join(ep_dir, f"episode_{episode.index}_{ext}.srt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(srt)
    return f"{settings.static_base_url}/media/multilingual/{episode.id}/episode_{episode.index}_{ext}.srt"


def build_voice_instruction(lang, emotion):
    base = "请用" + lang_display(lang) + "自然、有感情地朗读这段对白。"
    if emotion:
        base += "语气：" + emotion + "。"
    return base
