"""编排出口：统一按 mode 用帧数/模型/参考/提示词/时间轴原语组装生成规格 VideoSpec。

四个入口（单镜/幕级/画布导演台/项目连续长片）原则上只做「取分镜 → build_spec → 按模式派发回写」。
本模块先提供可复用的纯组装逻辑（spec 可 JSON 化）；入口的完整改造（尤其 episode_video_service）
属最终结构性步骤，需搭配 e2e 回归后接入。
"""
from __future__ import annotations

from .frames import duration_to_frames
from .timing import timeline_cuts


def build_spec(segments, *, mode: str, fps: int = 24, ref_max: int = 9,
               prompt_fn=None, db=None, model_resolver=None, ref_collector=None) -> dict:
    """用共享原语组装 VideoSpec。

    - prompt_fn(seg) -> str：每镜提示词（默认为 seg.description/前后镜拼接），可传入自定义编排。
    - model_resolver / ref_collector：可选注入（便于测试与后续接入 db）。
    - mode: single | episode | director | continuous。
    """
    specs = []
    for seg in segments:
        duration = float(getattr(seg, "duration", None) or 5.0)
        prompt = (prompt_fn(seg) if prompt_fn else (getattr(seg, "description", None) or ""))
        specs.append({
            "segment_id": str(getattr(seg, "id", None)),
            "prompt": prompt,
            "duration_sec": duration,
            "frames": duration_to_frames(duration, fps),
        })
    # 连续长片/导演台：按帧数算分段切点（供整片裁回分镜）
    timeline = None
    if mode in ("director", "continuous"):
        frame_list = [s["frames"] for s in specs]
        timeline = {"segments": specs, "cuts": timeline_cuts(frame_list, fps), "fps": fps}
    return {
        "mode": mode,
        "fps": fps,
        "ref_max": ref_max,
        "segments": specs,
        "timeline": timeline,
    }