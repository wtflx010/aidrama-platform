"""BGM 生成服务：情感曲线分析 → MusicGen API 生成 → 下载。

MusicGen 本地部署（Meta audiocraft，Apache 2.0），与 CosyVoice 共用 GPU。
API 接口：POST {musicgen_url}/generate → {audio_url, duration}
"""
import os
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.bgm import BgmTrack
from app.models.project import Episode
from app.models.segment import Segment

# 情感 → 音乐生成 prompt 映射
EMOTION_TO_MUSIC = {
    "温馨": "soft piano, warm strings, gentle melody, peaceful, 70bpm",
    "紧张": "dark electronic, low bass, pulsing rhythm, suspenseful, 120bpm",
    "悲伤": "sad cello, melancholic piano, slow, emotional, 60bpm",
    "欢快": "acoustic guitar, upbeat percussion, cheerful, 110bpm",
    "愤怒": "heavy drums, distorted guitar, aggressive, 140bpm",
    "恐惧": "ambient drone, dissonant, eerie, horror, 50bpm",
    "史诗": "orchestral, epic drums, cinematic, building, 90bpm",
    "平静": "ambient, soft pads, minimal, calm, 65bpm",
}


def emotion_to_music_prompt(emotion: str, duration: int) -> str:
    """情感标签 → MusicGen 生成 prompt。"""
    base = EMOTION_TO_MUSIC.get(emotion, EMOTION_TO_MUSIC["平静"])
    return f"{base}, instrumental, no vocals, {duration} seconds, cinematic background music"


def analyze_project_emotions(db: Session, project_id, episode_id=None) -> list[dict]:
    """分析项目各幕的情感走向，确定 BGM 需求。

    遍历每幕的所有分镜，从 description 推断主导情感（简化版：按关键词匹配）。
    返回：[{episode_id, emotion, duration, prompt}]

    episode_id 非空时只分析该幕。
    """
    stmt = select(Episode).where(Episode.project_id == project_id).order_by(Episode.index)
    if episode_id:
        stmt = stmt.where(Episode.id == episode_id)
    episodes = db.scalars(stmt).all()

    results = []
    for ep in episodes:
        segments = db.scalars(
            select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)
        ).all()
        if not segments:
            continue

        total_duration = sum(s.duration for s in segments)

        # 简化情感分析：扫描分镜描述中的情感关键词
        all_text = " ".join((s.description or "") + " " + (s.dialogue or "") for s in segments)
        dominant_emotion = _detect_emotion(all_text)

        bgm_duration = int(total_duration) + 5  # 多 5s 余量
        results.append({
            "episode_id": str(ep.id),
            "emotion": dominant_emotion,
            "duration": bgm_duration,
            "prompt": emotion_to_music_prompt(dominant_emotion, bgm_duration),
        })
    return results


# 情感关键词映射（简化版，后续可用 LLM 替代）
_EMOTION_KEYWORDS = {
    "悲伤": ["哭", "泪", "伤心", "失去", "离别", "孤独", "悲伤", "心痛"],
    "紧张": ["紧张", "危险", "逃跑", "追", "恐惧", "害怕", "黑暗", "颤抖"],
    "愤怒": ["愤怒", "生气", "吼", "打", "冲突", "争吵", "怒"],
    "欢快": ["笑", "开心", "快乐", "阳光", "欢", "幸福", "轻松"],
    "温馨": ["温暖", "温柔", "爱", "拥抱", "咖啡", "阳光", "花", "轻声"],
    "恐惧": ["恐怖", "鬼", "诡异", "阴森", "毛骨悚然", "惊恐"],
    "史诗": ["战斗", "战场", "英雄", "命运", "史诗", "壮烈", "冲锋"],
}


def _detect_emotion(text: str) -> str:
    """从文本中检测主导情感（关键词匹配，简化版）。"""
    scores = {}
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        scores[emotion] = sum(text.count(kw) for kw in keywords)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "平静"


def generate_bgm_track(prompt: str, duration: int) -> tuple[str, float]:
    """调用 MusicGen API 生成 BGM。

    返回：(本地音频相对路径, 时长)
    """
    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{settings.musicgen_url}/generate",
            json={"prompt": prompt, "duration": duration, "model": "medium"},
        )
        resp.raise_for_status()
        data = resp.json()

    audio_url = data["audio_url"]
    # 下载到本地
    filename = f"bgm_{uuid.uuid4().hex[:8]}.wav"
    subdir = os.path.join(settings.media_dir, "bgm")
    os.makedirs(subdir, exist_ok=True)
    filepath = os.path.join(subdir, filename)

    with httpx.Client(timeout=60) as client:
        audio_resp = client.get(audio_url)
        audio_resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(audio_resp.content)

    return f"bgm/{filename}", float(data.get("duration", duration))


def generate_project_bgm(db: Session, project_id, episode_id=None) -> list[BgmTrack]:
    """为项目所有幕（或单幕）生成 BGM。

    1. 分析各幕情感
    2. 调 MusicGen 生成
    3. 写入 BgmTrack 表
    """
    needs = analyze_project_emotions(db, project_id, episode_id=episode_id)
    tracks = []

    for need in needs:
        # 同一幕已有 done 状态的 BGM 则跳过（避免重复生成）
        existing = db.scalar(
            select(BgmTrack).where(
                BgmTrack.project_id == project_id,
                BgmTrack.episode_id == need["episode_id"],
                BgmTrack.status == "done",
            ).order_by(BgmTrack.created_at.desc())
        )
        if existing:
            tracks.append(existing)
            continue

        track = BgmTrack(
            project_id=project_id,
            episode_id=need["episode_id"],
            emotion=need["emotion"],
            prompt=need["prompt"],
            duration=need["duration"],
            status="generating",
        )
        db.add(track)
        db.flush()

        try:
            audio_path, dur = generate_bgm_track(need["prompt"], need["duration"])
            track.audio_url = f"{settings.static_base_url}/media/{audio_path}"
            track.duration = dur
            track.status = "done"
            track.error = None
        except Exception as e:
            track.status = "failed"
            track.error = str(e)
        tracks.append(track)

    db.commit()
    return tracks
