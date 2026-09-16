"""配音与字幕 API：TTS 配音生成（差异化）+ 字幕生成/编辑/列表 + 批量重跑
+ 预置声音列表 + 参考音频上传。"""
import base64
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas.batch import BatchBody
from app.schemas.voice import (
    SubtitleOut,
    SubtitleUpdate,
    VoiceBatchGenerateResp,
    VoiceGenerate,
    VoiceGenerateResp,
    VoiceLineOut,
)
from app.services import batch_voice_service, subtitle_service, voice_service

router = APIRouter()


# ===== 预置声音 =====

class VoicePresetOut(BaseModel):
    voice_id: str
    gender: str
    age_group: str
    sample_text: str


@router.get("/voice-presets", response_model=list[VoicePresetOut])
def list_voice_presets():
    """返回预置声音列表（供前端展示和选择）。"""
    from app.services.voice_preset_service import list_preset_voices
    return list_preset_voices()


# ===== 参考音频上传 =====

class AudioUploadBody(BaseModel):
    """base64 音频上传（避免 python-multipart 依赖）。"""
    filename: str
    data_base64: str  # 不带 data:前缀的纯 base64


class AudioUploadResp(BaseModel):
    url: str


# ===== 通用图片上传（分镜初始帧/画布参考等非资产场景复用） =====
# 2026-08-23 修复：分镜「初始帧手动上传」此前误用 /uploads/audio（只接受音频扩展名），
# 上传 PNG/JPG 必报 400。提供图片专用上传端点，校验扩展名与大小后落盘 static/media。
class ImageUploadBody(BaseModel):
    """base64 图片上传（避免 python-multipart 依赖）。"""
    filename: str
    data_base64: str  # 不带 data: 前缀的纯 base64


@router.post("/uploads/image", response_model=AudioUploadResp, status_code=201)
def upload_image(payload: ImageUploadBody):
    """上传参考图片（base64），保存到 media_dir/images_upload/，返回静态 URL。"""
    import re

    safe_name = re.sub(r"[^\w.\-]", "_", payload.filename or "")
    if not safe_name:
        raise HTTPException(400, "文件名无效")
    ext = Path(safe_name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        raise HTTPException(400, f"不支持的图片格式: {ext}")
    try:
        img_bytes = base64.b64decode(payload.data_base64)
    except Exception:
        raise HTTPException(400, "base64 解码失败")
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "图片文件过大（最大 10MB）")
    save_dir = Path(settings.media_dir) / "images_upload"
    save_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"{UUID(int=int.from_bytes(os.urandom(8), 'big')).hex[:8]}_{safe_name}"
    save_path = save_dir / unique_name
    save_path.write_bytes(img_bytes)
    url = f"{settings.static_base_url}/media/images_upload/{unique_name}"
    return AudioUploadResp(url=url)


@router.post("/uploads/audio", response_model=AudioUploadResp, status_code=201)
def upload_audio(payload: AudioUploadBody):
    """上传参考音频（base64），保存到 media_dir/voice_refs/，返回静态 URL。"""
    # 安全校验：文件名只允许字母数字下划线点横线
    import re
    safe_name = re.sub(r"[^\w.\-]", "_", payload.filename)
    if not safe_name:
        raise HTTPException(400, "文件名无效")
    # 限制扩展名
    ext = Path(safe_name).suffix.lower()
    if ext not in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        raise HTTPException(400, f"不支持的音频格式: {ext}")

    try:
        audio_bytes = base64.b64decode(payload.data_base64)
    except Exception:
        raise HTTPException(400, "base64 解码失败")

    # 限制大小（10MB）
    if len(audio_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "音频文件过大（最大 10MB）")

    save_dir = Path(settings.media_dir) / "voice_refs"
    save_dir.mkdir(parents=True, exist_ok=True)
    # 加 UUID 前缀防重名
    unique_name = f"{UUID(int=int.from_bytes(os.urandom(8), 'big')).hex[:8]}_{safe_name}"
    save_path = save_dir / unique_name
    save_path.write_bytes(audio_bytes)

    url = f"{settings.static_base_url}/media/voice_refs/{unique_name}"
    return AudioUploadResp(url=url)


# ===== 配音 =====

@router.get("/segments/{segment_id}/voice", response_model=list[VoiceLineOut])
def list_voice(segment_id: UUID, db: Session = Depends(get_db)):
    return voice_service.list_by_segment(db, segment_id)


@router.post("/segments/{segment_id}/voice/generate", response_model=list[VoiceGenerateResp], status_code=201)
def generate_voice(segment_id: UUID, payload: VoiceGenerate | None = None, db: Session = Depends(get_db)):
    """生成分镜差异化配音（多条 VoiceLine：N 对白 + 1 旁白）。"""
    try:
        results = voice_service.generate(db, segment_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return [VoiceGenerateResp(voiceline=vl, task=task) for vl, task in results]


# ===== 批量配音重跑 =====

@router.post("/episodes/{episode_id}/voice/batch-regenerate", response_model=VoiceBatchGenerateResp, status_code=201)
def batch_regenerate_episode_voice(episode_id: UUID, payload: BatchBody | None = None, db: Session = Depends(get_db)):
    """整幕配音批量重跑（删除既有 VoiceLine，按 dialogue_lines 重新生成）。"""
    try:
        task, total, sub_ids = batch_voice_service.batch_regenerate_episode(db, episode_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return VoiceBatchGenerateResp(task=task, total=total, sub_task_ids=sub_ids)


@router.post("/projects/{project_id}/voice/regenerate-all", response_model=VoiceBatchGenerateResp, status_code=201)
def batch_regenerate_project_voice(project_id: UUID, payload: BatchBody | None = None, db: Session = Depends(get_db)):
    """整项目配音批量重跑（跨幕）。"""
    try:
        task, total, sub_ids = batch_voice_service.batch_regenerate_project(db, project_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return VoiceBatchGenerateResp(task=task, total=total, sub_task_ids=sub_ids)


# ===== 字幕 =====

@router.get("/segments/{segment_id}/subtitles", response_model=list[SubtitleOut])
def list_subtitles(segment_id: UUID, db: Session = Depends(get_db)):
    return subtitle_service.list_by_segment(db, segment_id)


@router.post("/segments/{segment_id}/subtitle/generate", response_model=list[SubtitleOut], status_code=201)
def generate_subtitles(segment_id: UUID, db: Session = Depends(get_db)):
    try:
        return subtitle_service.generate(db, segment_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/subtitles/{subtitle_id}", response_model=SubtitleOut)
def update_subtitle(subtitle_id: UUID, payload: SubtitleUpdate, db: Session = Depends(get_db)):
    try:
        return subtitle_service.update(db, subtitle_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
