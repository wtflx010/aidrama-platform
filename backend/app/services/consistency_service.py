"""角色身份一致性质检（P0-2）：消费 H3 retention_analysis + 角色黄金参考图锚点。

对标 Higgsfield soul-id + retention_analysis：
- 从分镜视频增强 prompt 的 retention_analysis 段解析每个 <Subject N> 的保留状态
  （fully_preserved / partially_preserved / attribute_transfer / weak_reference），
  算出该镜（及该集）的角色一致性分数；
- 维护每个角色的「黄金参考图」锚点集合（character_sheet / 封面 / 正视图等稳定源），
  供关键帧生成装配参考与质检比对；
- 人脸一致性质检为可插拔：优先用可选的人脸 embed 引擎，缺失时降级为感知哈希
  粗粒度守护（无硬依赖）。
"""
import logging
import re

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_RETENTION_WEIGHT = {
    "fully_preserved": 1.0,
    "partially_preserved": 0.6,
    "attribute_transfer": 0.4,
    "weak_reference": 0.1,
}

_SUBJECT_RETENTION_RE = re.compile(
    r"<Subject\s+(\d+)>(?:\s*\([^)]*\))?\s*:\s*(fully_preserved|partially_preserved|attribute_transfer|weak_reference)",
    re.IGNORECASE,
)


def split_retention_section(prompt: str) -> str:
    if not prompt:
        return ""
    m = re.search(r"(?im)^\s*retention_analysis\s*:", prompt)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"(?im)^\s*(summary|detailed_description|overall_soundscape|non_diegetic_music)\s*:", prompt[start:])
    end = start + nxt.start() if nxt else len(prompt)
    return prompt[start:end]


def parse_retention_analysis(prompt: str) -> list[dict]:
    section = split_retention_section(prompt)
    if not section:
        return []
    out = []
    seen = set()
    for m in _SUBJECT_RETENTION_RE.finditer(section):
        subject = int(m.group(1))
        status = m.group(2).lower()
        w = _RETENTION_WEIGHT.get(status, 0.1)
        if subject in seen:
            continue
        seen.add(subject)
        out.append({"subject": subject, "status": status, "weight": w})
    return out


def segment_consistency(segment) -> dict:
    prompt = (getattr(segment, "enhanced_prompt", None) or "").strip()
    subs = parse_retention_analysis(prompt)
    if not subs:
        return {"score": None, "weak": [], "per_subject": [], "source": "none"}
    weak = [s["subject"] for s in subs if s["weight"] < 0.5]
    score = round(sum(s["weight"] for s in subs) / len(subs), 2) if subs else None
    return {"score": score, "weak": weak, "per_subject": subs, "source": "retention"}


def episode_consistency(episode) -> dict:
    segs = sorted(episode.segments, key=lambda s: s.index)
    per_segment = []
    weak_segments = []
    scores = []
    for s in segs:
        c = segment_consistency(s)
        item = {"segment_index": s.index, "segment_id": str(s.id),
                "score": c["score"], "weak": c["weak"], "source": c["source"]}
        per_segment.append(item)
        if c["score"] is not None and c["score"] < 0.6:
            weak_segments.append({"segment_index": s.index, "score": c["score"], "weak": c["weak"]})
        if c["score"] is not None:
            scores.append(c["score"])
    return {
        "episode_id": str(episode.id),
        "segments": per_segment,
        "weak_segments": weak_segments,
        "overall_score": round(sum(scores) / len(scores), 2) if scores else None,
        "evaluated_segments": sum(1 for c in per_segment if c["score"] is not None),
        "total_segments": len(segs),
    }


def character_anchor_urls(asset) -> list[str]:
    if asset is None:
        return []
    anchors: list[str] = []
    for field in ("character_sheet_url", "cover_url"):
        u = getattr(asset, field, None)
        if u:
            anchors.append(u)
    fv = getattr(asset, "four_view_urls", None) or []
    if isinstance(fv, list):
        anchors.extend(u for u in fv[:2] if u)
    states = getattr(asset, "states", None) or []
    if isinstance(states, list):
        for st in states:
            u = (st or {}).get("image_url")
            if u:
                anchors.append(u)
    seen, out = set(), []
    for u in anchors:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


_FACE_ENGINE = None
_FACE_TRIED = False


def _load_face_engine():
    global _FACE_ENGINE, _FACE_TRIED
    if _FACE_TRIED:
        return _FACE_ENGINE
    _FACE_TRIED = True
    try:
        import insightface  # type: ignore
        import numpy as np  # type: ignore
        from PIL import Image

        model = insightface.app.FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        model.prepare(ctx_id=0)

        def _sim(a: str, b: str):
            def _emb(p):
                img = Image.open(p).convert("RGB")
                arr = np.asarray(img)
                faces = model.get(arr)
                return faces[0].normed_embedding if faces else None
            ea, eb = _emb(a), _emb(b)
            if ea is None or eb is None:
                return None
            return float(np.dot(ea, eb))

        _FACE_ENGINE = _sim
        return _sim
    except Exception as e:  # noqa: BLE001
        logger.info("[consistency] 人脸 embed 引擎不可用（降级感知哈希）: %s", e)
        _FACE_ENGINE = None
        return None


def face_consistency(image_path: str, anchor_path: str) -> dict:
    from app.utils import image_sim

    engine = _load_face_engine()
    if engine is not None:
        try:
            sim = engine(image_path, anchor_path)
            if sim is not None:
                return {"engine": "face", "similarity": round(max(0.0, min(1.0, sim)), 3), "note": ""}
        except Exception as e:  # noqa: BLE001
            logger.warning("[consistency] 人脸引擎比对失败，降级 phash: %s", e)
    try:
        h_img = image_sim.phash(image_path)
        h_anchor = image_sim.phash(anchor_path)
        d = image_sim.hamming(h_img, h_anchor)
        sim = max(0.0, 1.0 - d / 64.0)
        return {"engine": "phash", "similarity": round(sim, 3),
                "note": "感知哈希粗粒度（整图布局相似度，非严格人脸）"}
    except Exception as e:  # noqa: BLE001
        return {"engine": "none", "similarity": None, "note": "无法评估: " + str(e)}
