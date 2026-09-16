"""提示词原语：把分镜「对白/旁白」按镜头实际落成画面口播/画外音指令（H3 原生说话）。

收敛自 project_longvideo_service._prompt_with_speech / _is_face_talking_shot（连续长片与后续入口共用）。
原则：能露脸说话（特写/近景/正对）→ 画面人物开口；远景/背影/全景 → 旁白/内心独白（画外音）。
单条台词必须在一个镜头内完整说完：超容量则用「提速说完 + 只念引号内」引导，避免掐尾致语速飘/自补词。
"""
from __future__ import annotations

from app.constants import CHARS_PER_SEC


def is_face_talking_shot(seg) -> bool:
    """镜头是否为「人物正面/近景·口型可见」的说话特写。"""
    desc = " ".join([getattr(seg, "description", None) or "", getattr(seg, "camera", None) or "", getattr(seg, "shot_type", None) or ""])
    face = any(k in desc for k in ("特写", "近景", "正对镜头", "面向镜头", "对镜头", "正面", "直视镜头", "嘴唇", "口型", "凝视镜头"))
    far = any(k in desc for k in ("远景", "全景", "大远景", "背影", "眺望", "回望", "远摄", "侧影", "全身", "远处", "朝远处"))
    return face and not far


def prompt_with_speech(seg, prompt: str) -> str:
    """把该分镜的「对白 / 旁白」写进画面提示词，让 H3 按镜头实际情况说。"""
    parts = [prompt.strip()] if prompt and prompt.strip() else []
    has = {d for d in parts}
    onscreen = is_face_talking_shot(seg)
    try:
        dur = float(getattr(seg, "duration", 0) or 5)
    except (TypeError, ValueError):
        dur = 5.0
    capacity = max(4, int((dur - 0.4) * CHARS_PER_SEC))
    _PACING_HINT = "【口播要求】本句需在该镜头时长内完整说完：语速稍快、干脆利落，一字不落；不要拖长音、不要中断、不要省略，也不要在台词外自行添加或重复任何词。"
    _ONLY_SAID = "角色只念出「」内这一段台词，除此之外不开口说任何话、不重复、不加词。"

    def _say(text: str) -> str:
        over = len(text) > capacity
        base = (
            (f"{speaker}正对镜头开口说话，口型与台词同步：「{text}」" if onscreen
             else f"旁白（{speaker}的内心独白，画外音，非画面人物开口）：{text}")
        )
        if over:
            base += f" {_PACING_HINT}"
        return base + " " + _ONLY_SAID

    for dl in (seg.dialogue_lines or []):
        text = (dl.get("text") or "").strip()
        if not text or any(text in h for h in has):
            continue
        speaker = (dl.get("speaker") or "角色").strip()
        parts.append(_say(text))
        has.add(text)
    nar = (seg.narration or "").strip()
    if nar and not any(nar in h for h in has):
        speaker = "旁白"
        parts.append(_say(nar))
    return "\n".join(p for p in parts if p)