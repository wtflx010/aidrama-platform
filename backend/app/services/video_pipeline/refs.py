"""资产/参考图收集原语：跨分镜去重收集公共参考图（角色/场景/道具），上限 ref_max 张。

收敛自 canvas_director._collect_global_refs（画布导演台 & 项目连续长片共用）。
注：幕级 episode_video_service 使用不同的项目/分镜级参考策略(resolve_r2v_refs/_resolve_design_refs)，
属后续收敛范围，本原语不强制合并以保持其行为不变。
"""
from __future__ import annotations

from sqlalchemy.orm import Session


def collect_global_refs(db: Session, segments, ref_max: int = 9) -> list[dict]:
    """跨分镜去重收集公共参考图，返回 [{"index","imageFile","label"}]，上限 ref_max 张。"""
    from app.tasks.generate_video import resolve_shot_asset_refs

    seen: list[dict] = []
    seen_urls: set[str] = set()
    for seg in segments:
        for url, label in resolve_shot_asset_refs(db, seg, ref_max=ref_max):
            if url in seen_urls or len(seen) >= ref_max:
                continue
            seen_urls.add(url)
            seen.append({"index": len(seen), "imageFile": url, "label": label})
    return seen
