"""成片评估服务（P0-1）：规则分 + LLM 四维分（对标 Higgsfield Virality Predictor）。

对一集成片打「钩子 hook / 注意力 attention / 留存 retention / 病毒性 virality」
四维分（1~10），产出文字报告与逐分镜反哺建议，持久化到 EpisodeEval。
"""
import base64
import json
import logging
import os
import subprocess
import tempfile

from sqlalchemy.orm import Session

from app.config import settings
from app.models.episode_eval import EpisodeEval, EvalStatus
from app.models.project import Episode
from app.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

_HOOK_BOOST_EMOTION = {"愤怒", "紧张", "恐惧", "震惊", "史诗"}
_HOOK_PENALTY_EMOTION = {"平静", "温馨"}
_CLIFFHANGER_HINTS = ("？", "?", "突然", "竟然", "没想到", "却", "转身", "消失")


def _clamp(v, lo=1.0, hi=10.0):
    return max(lo, min(hi, v))


def collect_segments(db, episode):
    segs = sorted(episode.segments, key=lambda s: s.index)
    out = []
    for s in segs:
        lines = (s.dialogue_lines or []) or []
        dialogue = "；".join(
            (str(d.get('speaker','')) + ':' + str(d.get('text',''))) for d in lines if d.get("text")
        ) or (s.dialogue or "")
        sub_text = " ".join((sub.text or "") for sub in (getattr(s, "subtitles", None) or []) or [])
        out.append({
            "index": s.index,
            "shot_type": s.shot_type or "",
            "camera": s.camera or "",
            "emotion": s.emotion or "",
            "description": (s.description or "").strip()[:400],
            "dialogue": dialogue.strip()[:200],
            "subtitle": sub_text.strip()[:200],
            "enhanced_prompt": (s.enhanced_prompt or "")[:800],
        })
    return out


def episode_video_url(db, episode):
    urls = [
        r.video_url for r in sorted(episode.episode_videos, key=lambda x: x.index)
        if r.video_url
    ]
    return ",".join(urls) if urls else None


def _video_to_local(video_url: str | None) -> str | None:
    """把对外媒体 URL 还原为本地磁盘路径（供 ffmpeg 抽帧）；非本地媒体返回 None。"""
    if not video_url:
        return None
    marker = "/static/media/"
    if marker in video_url:
        return os.path.join(settings.media_dir, video_url.split(marker, 1)[1])
    return None


def _probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True, timeout=60,
        )
        return float(out.stdout.strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _sample_video_frames(video_url: str | None, n: int = 5, max_side: int = 480) -> list[str]:
    """从成片视频均匀抽取 n 帧为 base64 JPEG data URI，供视觉模型打分。

    任一步失败（本地无文件/ffmpeg 缺失/探测失败）返回空列表 —— 调用方据此回退纯文本评分。
    """
    path = _video_to_local(video_url)
    if not path or not os.path.exists(path):
        return []
    dur = _probe_duration(path)
    if dur <= 0:
        return []
    uris: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as td:
            for i in range(n):
                t = dur * (i + 0.5) / n
                out = os.path.join(td, f"f{i}.jpg")
                r = subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                     "-vf", f"scale=trunc(iw/2)*2:-2", "-q:v", "4", out],
                    capture_output=True, timeout=60,
                )
                if r.returncode != 0 or not os.path.exists(out):
                    continue
                with open(out, "rb") as fh:
                    uris.append("data:image/jpeg;base64," + base64.b64encode(fh.read()).decode())
    except Exception:  # noqa: BLE001
        return []
    return uris


def rule_scores(segments):
    if not segments:
        return {"hook": 5, "attention": 5, "retention": 5, "virality": 5,
                "overall": 5, "details": {"segments": 0, "note": "无分镜"}}
    first = segments[0]
    last = segments[-1]
    hook = 10.0
    if not first.get("dialogue"):
        hook -= 3.0
    if first.get("emotion") in _HOOK_PENALTY_EMOTION:
        hook -= 2.0
    if first.get("shot_type") in ("远景", "全景", "远景/全景"):
        hook -= 2.0
    if first.get("emotion") in _HOOK_BOOST_EMOTION:
        hook += 2.0
    if first.get("enhanced_prompt") and any(k in first["enhanced_prompt"].lower()
                                            for k in ("push in", "truck", "track", "fast whip", "action")):
        hook += 1.0

    end_hook = 10.0
    end_text = (last.get("dialogue") or last.get("subtitle") or "")
    if end_text and any(h in end_text for h in _CLIFFHANGER_HINTS):
        end_hook += 1.0
    else:
        end_hook -= 1.0

    emotions = {s.get("emotion") for s in segments if s.get("emotion")}
    variety = min(3.0, len(emotions))
    retention = 10.0 - max(0, len(segments) - 18) * 0.3 + variety * 0.5
    if not any(s.get("dialogue") for s in segments):
        retention -= 1.5

    attention = hook * 0.6 + retention * 0.4
    virality = 5.0 + (1.0 if first.get("emotion") in _HOOK_BOOST_EMOTION else 0.0)         + (1.0 if any(h in (last.get("dialogue") or "") for h in _CLIFFHANGER_HINTS) else 0.0)

    scores = {
        "hook": round(_clamp(hook), 1),
        "attention": round(_clamp(attention), 1),
        "retention": round(_clamp(retention), 1),
        "virality": round(_clamp(virality), 1),
        "overall": round(_clamp((hook + attention + retention + virality) / 4), 1),
    }
    scores["details"] = {
        "segments": len(segments),
        "hook_rule": "首镜情绪=" + (first.get('emotion') or '无') + " 对白=" + ("有" if first.get('dialogue') else "无") + " 景别=" + (first.get('shot_type') or '无'),
        "ending_rule": "末镜留钩=" + ("有" if any(h in (last.get('dialogue') or '') for h in _CLIFFHANGER_HINTS) else "无"),
        "emotion_variety": len(emotions),
    }
    return scores


def _build_llm_messages(episode, segments):
    lines = []
    for s in segments:
        seg = ("[镜" + str(s['index']) + "] 景别=" + (s['shot_type'] or '无') + " 情绪=" + (s['emotion'] or '无') +
               " 画面=" + (s['description'] or '无'))
        if s["dialogue"]:
            seg += " 对白=" + s["dialogue"]
        lines.append(seg)
    shot_context = "\n".join(lines) if lines else "（无分镜）"
    prompt = (
        "你是短视频/微短剧的成片质量评估专家。下面是「" + (episode.title or "") + "」一集的分镜信息（按拍摄顺序），"
        "以及它对应的最终成片。请从短视频传播角度，对成片四个维度各打 1~10 分，并给出可执行的优化建议。\n"
        "\n【分镜信息】\n" + shot_context +
        "\n\n【任务】\n- 判断这集的成片是否适合作为 AI 漫剧/微短剧短内容分发。\n"
        "- 四维定义：\n  hook（开头钩子，前 3 秒是否抓人）\n"
        "  attention（注意力，画面/情绪/对白能否持续拉住观众）\n"
        "  retention（留存，中段与结尾是否让人看完并期待下一集）\n"
        "  virality（病毒性，是否易被分享/讨论/二创）\n"
        "\n【输出格式】只输出纯 JSON（不要 markdown、不要解释）：\n"
        '{"hook": 分, "attention": 分, "retention": 分, "virality": 分, "overall": 分,'
        ' "report": "一段中文评估报告（指出强项与短板，30~80 字）",'
        ' "suggestions": [{"segment_index": 镜序号(必须是上面分镜信息里出现的数字), "issue": "问题", "suggestion": "具体提示词层面的改进建议"}]}\n'
        "suggestions 最多 3 条，segment_index 只能取上面出现的镜号；suggestion 要能直接用于改该镜的生成提示词"
        "（如：改成强冲突动作开场、前 3 秒加入强对白钩子、把冷开场改成悬念、结尾留钩等）。"
    )
    return [
        {"role": "system", "content": "你是短视频成片质量评估专家，只输出符合要求的 JSON。"},
        {"role": "user", "content": prompt},
    ]


def _extract_json(text):
    s = text.strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("LLM 未返回 JSON")
    return json.loads(s[i:j + 1])


def llm_score(db, episode, segments, video_url=None):
    from app.services.prompt_enhance_service import _resolve_text_model

    indices = {s["index"] for s in segments}
    model = _resolve_text_model(db, None, "script")
    provider = ProviderRegistry.for_model(model)
    text_messages = _build_llm_messages(episode, segments)
    messages = text_messages
    # 视觉打分：从成片抽帧并入 user content（模型支持视觉才生效）；任何失败回退纯文本
    frames = _sample_video_frames(video_url) if video_url else []
    if frames:
        try:
            last = text_messages[-1]
            content = [{"type": "text", "text": last["content"]}]
            content += [{"type": "image_url", "image_url": {"url": f}} for f in frames]
            messages = [
                {"role": "system", "content": text_messages[0]["content"]},
                {"role": "user", "content": content},
            ]
            resp = provider.chat(messages)
            content = resp["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001 - 视觉失败回退纯文本
            resp = provider.chat(text_messages)
            content = resp["choices"][0]["message"]["content"]
    else:
        resp = provider.chat(text_messages)
        content = resp["choices"][0]["message"]["content"]
    data = _extract_json(content)
    scores = {k: _clamp(float(data.get(k, 5))) for k in ("hook", "attention", "retention", "virality", "overall")}
    report = str(data.get("report") or "").strip()
    raw_sugs = data.get("suggestions") or []
    suggestions = [
        {
            "segment_index": int(s.get("segment_index")),
            "issue": str(s.get("issue") or "").strip(),
            "suggestion": str(s.get("suggestion") or "").strip(),
        }
        for s in raw_sugs
        if isinstance(s, dict) and s.get("segment_index") in indices
    ][:3]
    return scores, report, suggestions


def evaluate_episode(db, episode):
    segments = collect_segments(db, episode)
    rule = rule_scores(segments)
    scores = {k: rule.get(k, 5) for k in ("hook", "attention", "retention", "virality", "overall")}
    report, suggestions = "", []
    try:
        llm_scores, llm_report, llm_sugs = llm_score(db, episode, segments, episode_video_url(db, episode))
        scores.update(llm_scores)
        report = llm_report
        suggestions = llm_sugs
    except Exception as e:
        logger.warning("[evaluate] 幕 %s LLM 评分失败，回退规则分: %s", episode.id, e)
        report = "LLM 评估失败，以下为规则分结果。"
    return {
        "video_url": episode_video_url(db, episode),
        "scores": scores,
        "rule_scores": rule,
        "report": report,
        "suggestions": suggestions,
    }


def new_eval_row(db, episode_id):
    row = EpisodeEval(episode_id=episode_id, scores={}, rule_scores={}, suggestions=[], status=EvalStatus.running)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_result(db, row, result):
    row.video_url = result["video_url"]
    row.scores = result["scores"]
    row.rule_scores = result["rule_scores"]
    row.report = result["report"]
    row.suggestions = result["suggestions"]
    row.status = EvalStatus.succeeded
    db.commit()


def fail(db, row, error):
    row.status = EvalStatus.failed
    row.error = error
    db.commit()


def latest(db, episode_id):
    return db.query(EpisodeEval).filter(EpisodeEval.episode_id == episode_id).order_by(EpisodeEval.created_at.desc()).first()
