"""分镜内多镜头运镜节拍（shot_beats）工具。

结构：list[dict]，每项 {start_sec, end_sec, shot_type, camera, content}
- 时间连续覆盖 0~duration：第一拍从 0.00s 起、最后一拍止于 duration，拍与拍之间无缝衔接；
- 空列表 = 整镜单镜头（沿用 segment 的 shot_type/camera 单值），增强层不强制切镜；
- 2 拍及以上才视为「分镜内多镜头运镜」：驱动 prompt_enhance_service 把节拍
  严格转成 H3 官方时间码 [Shot N] At MM:SS.mmm。

2026-08-28 新增。
"""
from __future__ import annotations

import hashlib
import json

# 允许的景别/运镜枚举（单一来源在 app.services.shot_grammar，避免跨模块重复定义漂移）
from app.services.shot_grammar import CAMERAS, SHOT_TYPES


def normalize_shot_beats(
    beats,
    duration: float,
    default_shot_type: str | None = None,
    default_camera: str | None = None,
) -> list[dict]:
    """归一化节拍列表，返回可直接落库/注入 prompt 的干净结构。

    - None/空/非法结构 → []
    - 非法项（缺数字时间、起≥止、超出 0~duration）剔除
    - 景别/运镜不在枚举内或缺失 → 回退默认（segment 单值，再缺用「中景/固定」）
    - 时间按 start_sec 排序后自愈为连续铺满：首拍 0.0s 起、末拍止于 duration、
      中间拍结束点 = 下一拍起点（无缝衔接，杜绝 H3 切镜时间空档/重叠）
    - content 为节拍内画面内容（中文，增强层转英文）
    """
    if not beats or not isinstance(beats, list):
        return []
    try:
        duration = max(0.0, float(duration or 0.0))
    except (TypeError, ValueError):
        return []
    if duration <= 0:
        return []

    def _pick(value, allowed, fallback):
        v = str(value or "").strip()
        return v if v in allowed else (fallback or allowed[0])

    cleaned: list[dict] = []
    for b in beats:
        if not isinstance(b, dict):
            continue
        try:
            start = float(b.get("start_sec"))
            end = float(b.get("end_sec"))
        except (TypeError, ValueError):
            continue
        if not (0.0 <= start < end):
            continue
        cleaned.append({
            "start_sec": round(max(0.0, min(duration, start)), 2),
            "end_sec": round(max(0.0, min(duration, end)), 2),
            "shot_type": _pick(b.get("shot_type"), SHOT_TYPES, default_shot_type),
            "camera": _pick(b.get("camera"), CAMERAS, default_camera),
            "content": str(b.get("content") or "").strip(),
        })
    if not cleaned:
        return []
    cleaned.sort(key=lambda x: x["start_sec"])
    out: list[dict] = []
    cursor = 0.0
    for i, b in enumerate(cleaned):
        # 起点延续上一拍的结束点（无缝衔接）：LLM/用户给出的时间有空档/重叠时
        # 自动收敛为连续覆盖，杜绝 H3 切镜时间空档或重叠
        start = cursor
        if start >= duration - 1e-9:
            # 已排满本镜时长，后续节拍放不下 → 丢弃（避免生成零长/越界拍）
            break
        end_raw = duration if i == len(cleaned) - 1 else b["end_sec"]
        end = min(max(start + 0.01, end_raw), duration)
        out.append({
            **b,
            "start_sec": round(start, 2),
            "end_sec": round(end, 2),
        })
        cursor = end
    return out


def format_beats_block(beats: list[dict] | None, duration: float | None = None) -> str:
    """构造注入增强模板的【节拍计划】块（中文，LLM 转英文 [Shot N]）。

    拍数 < 2 或空 → 空串（表示整镜单镜头，增强层自由发挥切镜）。
    """
    beats = beats or []
    if len(beats) < 2:
        return ""
    dur_hint = f"（本镜总时长 {float(duration or 0):g}s）" if duration else ""
    lines = [f"【节拍计划{dur_hint}——本镜为分镜内多镜头运镜，必须严格执行】"]
    for i, b in enumerate(beats, 1):
        st = b.get("shot_type") or "中景"
        cam = b.get("camera") or "固定"
        content = (b.get("content") or "").strip()
        lines.append(
            f"- 节拍 {i}：{b.get('start_sec', 0):g}s ~ {b.get('end_sec', 0):g}s，"
            f"景别={st}，运镜={cam}：{content}"
        )
    return "\n".join(lines)


def beats_fingerprint(beats: list[dict] | None) -> str:
    """节拍指纹（md5 前 8 位），纳入增强缓存键：节拍变化 → 旧缓存自动失效。"""
    return hashlib.md5(
        json.dumps(beats or [], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
