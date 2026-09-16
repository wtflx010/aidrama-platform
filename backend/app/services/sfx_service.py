"""音效业务服务。

双轨生成：
1. annotate_segments — LLM 标注每个分镜需要的音效类型/名称（ footsteps/door/rain/wind/...）
2. generate_sfx_clips — 调 Freesound API 检索下载音效，写入 SfxClip 表

Freesound API：https://freesound.org/docs/api/
- 搜索：GET /search/text/?query={q}&fields=id,duration,previews
- 预览音轨：preview_url（mp3）
- 鉴权：Authorization: Token {api_key}

无 FREESOUND_API_KEY 时返回友好错误，不阻断流程。
"""
import os
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.model_config import ModelType
from app.models.segment import Segment
from app.models.sfx import SfxClip
from app.providers.errors import map_to_chinese
from app.services.keyframe_service import _resolve_model

# Freesound API
FREESOUND_BASE = "https://freesound.org/apiv2"

# LLM 标注 prompt：从分镜描述提取音效需求
SFX_ANNOTATE_PROMPT = """你是一名专业音效设计师。请分析以下分镜，标注需要的音效。

要求返回纯 JSON（不要 markdown 代码块、不要任何解释文字），结构如下：
{{
  "sfx": [
    {{
      "sfx_type": "音效类型英文标识，从以下选择：footsteps/door/rain/wind/thunder/explosion/glass/bell/horror/laughter/clock/water/fire/birds/city/keyboard/phone/silence/other",
      "sfx_name": "中文音效名称，如'脚步声-木地板'",
      "start_time": 0.0,
      "volume": 0.5,
      "reason": "为什么需要这个音效（简短）"
    }}
  ]
}}

规则：
- 没有合适音效时返回空数组 []
- 通常每镜 0~3 个音效
- start_time 在该镜时长范围内（秒）
- volume 0.0~1.0，背景音效（如环境）0.2~0.3，事件音效 0.5~0.8
- 优先标注关键事件音效，避免过度堆叠

分镜信息：
- 描述：{description}
- 对白：{dialogue}
- 旁白：{narration}
- 景别：{shot_type}
- 运镜：{camera}
- 时长：{duration}秒
- 情感：{emotion}"""


def annotate_segments(db: Session, project_id, segment_ids=None, model_id=None) -> list[dict]:
    """LLM 标注分镜音效需求。

    返回：[{segment_id, sfx: [{sfx_type, sfx_name, start_time, volume, reason}]}]
    """
    from app.models.project import Episode
    from app.providers.registry import ProviderRegistry
    from app.services.llm_script_service import _extract_json

    stmt = (
        select(Segment)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
        .order_by(Episode.index, Segment.index)
    )
    if segment_ids:
        stmt = stmt.where(Segment.id.in_(segment_ids))
    segments = db.scalars(stmt).all()

    if not segments:
        return []

    model = _resolve_model(db, model_id, ModelType.text, "script")
    provider = ProviderRegistry.for_model_id(db, model.id)

    results: list[dict] = []
    for seg in segments:
        prompt = SFX_ANNOTATE_PROMPT.format(
            description=seg.description or "",
            dialogue=seg.dialogue or "",
            narration=seg.narration or "",
            shot_type=seg.shot_type or "",
            camera=seg.camera or "",
            duration=seg.duration or 5.0,
            emotion=getattr(seg, "emotion", "") or "",
        )
        try:
            resp = provider.chat([{"role": "user", "content": prompt}])
            content = resp["choices"][0]["message"]["content"]
            data = _extract_json(content)
            sfx_list = data.get("sfx", []) if isinstance(data, dict) else []
            results.append({
                "segment_id": str(seg.id),
                "sfx": sfx_list,
            })
        except Exception as e:
            results.append({
                "segment_id": str(seg.id),
                "sfx": [],
                "error": map_to_chinese(e),
            })
    return results


def search_freesound(query: str, api_key: str | None = None, max_results: int = 5) -> dict | None:
    """检索 Freesound，返回最佳匹配的预览音轨信息。

    返回：{id, duration, preview_url, name} 或 None
    """
    api_key = api_key or settings.freesound_api_key
    if not api_key:
        return None
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{FREESOUND_BASE}/search/text/",
            params={
                "query": query,
                "fields": "id,duration,previews,name",
                "page_size": max_results,
                "filter": "duration:[1 TO 30]",  # 1~30 秒
            },
            headers={"Authorization": f"Token {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results", [])
    if not results:
        return None
    best = results[0]
    preview_url = best.get("previews", {}).get("preview-lq-mp3") or \
                  best.get("previews", {}).get("preview-hq-mp3")
    return {
        "id": best["id"],
        "duration": float(best.get("duration", 0)),
        "preview_url": preview_url,
        "name": best.get("name", "")[:128],
    }


def download_sfx_audio(preview_url: str) -> tuple[str, float | None]:
    """下载 Freesound 预览 mp3 到本地。

    返回：(本地相对路径, 时长或 None)
    """
    filename = f"sfx_{uuid.uuid4().hex[:8]}.mp3"
    subdir = os.path.join(settings.media_dir, "sfx")
    os.makedirs(subdir, exist_ok=True)
    filepath = os.path.join(subdir, filename)

    with httpx.Client(timeout=60) as client:
        resp = client.get(preview_url)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(resp.content)

    # 探测时长
    duration = None
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", filepath],
            capture_output=True, timeout=10,
        )
        duration = float(r.stdout.decode().strip())
    except Exception:
        pass

    return f"sfx/{filename}", duration


def generate_sfx_clips(db: Session, project_id, segment_ids=None, model_id=None) -> list[SfxClip]:
    """为项目分镜生成 SFX 音效片段。

    流程：LLM 标注 → Freesound 检索 → 下载 → 写 SfxClip。
    """
    annotations = annotate_segments(db, project_id, segment_ids=segment_ids, model_id=model_id)
    clips: list[SfxClip] = []

    for ann in annotations:
        segment_id = ann["segment_id"]
        for sfx in ann.get("sfx", []):
            sfx_type = sfx.get("sfx_type", "other")
            sfx_name = sfx.get("sfx_name", sfx_type)
            start_time = float(sfx.get("start_time", 0.0))
            volume = float(sfx.get("volume", 0.5))

            clip = SfxClip(
                segment_id=segment_id,
                sfx_type=sfx_type,
                sfx_name=sfx_name,
                start_time=start_time,
                volume=volume,
                status="generating",
            )
            db.add(clip)
            db.flush()

            if not settings.freesound_api_key:
                clip.status = "failed"
                clip.error = "未配置 FREESOUND_API_KEY"
                clips.append(clip)
                continue

            try:
                # 用 sfx_name（中文名）检索效果差，优先用 sfx_type 英文
                query = sfx_type if sfx_type != "other" else sfx_name
                result = search_freesound(query)
                if not result:
                    clip.status = "failed"
                    clip.error = f"Freesound 未找到匹配音效：{query}"
                    clips.append(clip)
                    continue

                audio_path, duration = download_sfx_audio(result["preview_url"])
                clip.audio_url = f"{settings.static_base_url}/media/{audio_path}"
                clip.duration = duration or 0.0
                clip.status = "done"
                clip.error = None
            except Exception as e:
                clip.status = "failed"
                clip.error = str(e)
            clips.append(clip)

    db.commit()
    return clips
