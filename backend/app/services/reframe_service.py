"""修片工作流服务（P1-4）：reframe 画幅重切 + voice-change 换声轨 + draw-to-video 探测。

对标 Higgsfield reframe / draw-to-video / voice-change：
- reframe：对已成片做目标画幅裁剪/缩放（ffmpeg，无需模型）；
- voice-change：用指定语音朗读文本，替换成片音轨（TTS + ffmpeg 混流）；
- draw-to-video：草图局部重绘（需支持该能力的模型/workflow，未配置则明确报错）。
"""
import logging
import os
import subprocess
import uuid

from app.config import settings

logger = logging.getLogger(__name__)

_RESOLUTION_HEIGHT = {"480p": 480, "720p": 720, "1080p": 1080}


def url_to_local(url):
    """静态 URL → 本地文件路径（media 目录）。"""
    if not url:
        raise ValueError("媒体地址为空")
    return url.replace(f"{settings.static_base_url}/media/", f"{settings.media_dir}/")


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)


def _probe(path):
    """ffprobe 取 宽/高/时长。返回 (w, h, duration) 或抛错。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", path],
        check=True, capture_output=True, timeout=60,
    )
    import json
    data = json.loads(r.stdout.decode())
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    return int(st.get("width") or 0), int(st.get("height") or 0), float(fmt.get("duration") or 0)


def _target_dims(ratio: str, resolution: str) -> tuple[int, int]:
    """解析 '9:16' 与 '720p' → (W, H)。"""
    rp = (ratio or "16:9").strip().lower()
    try:
        rw, rh = (int(x) for x in rp.replace("：", ":").split(":"))
    except ValueError:
        raise ValueError(f"宽高比格式非法: {ratio}")
    h = _RESOLUTION_HEIGHT.get((resolution or "720p").strip().lower(), 720)
    w = round(h * rw / rh)
    if w < 1:
        raise ValueError(f"宽高比非法: {ratio}")
    return w, h


def reframe(video_url: str, target_ratio: str = "9:16", resolution: str = "720p",
            out_name: str = "") -> str:
    """对成片做画幅重切：居中裁剪 + 缩放，输出新片 URL。"""
    src = url_to_local(video_url)
    if not os.path.exists(src):
        raise ValueError("源视频文件不存在")
    w, h = _target_dims(target_ratio, resolution)
    out_dir = os.path.join(settings.media_dir, "reframe", str(uuid.uuid4()))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name or f"reframed_{target_ratio.replace(':','x')}.mp4")
    # 先放大铺满再居中裁剪：scale 到覆盖目标，再 crop 中心
    vf = (
        f"scale=ceil({w}/2)*2:ceil({h}/2)*2:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2"
    )
    _run(["ffmpeg", "-y", "-i", src, "-vf", vf, "-c:a", "copy", "-an",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path])
    return f"{settings.static_base_url}/media/reframe/{os.path.basename(out_dir)}/{os.path.basename(out_path)}"


def synthesize_speech(db, text: str, voice_id: str | None, emotion: str | None) -> str:
    """用 TTS 生成一段旁白音频（本地文件路径）。"""
    from app.models.model_config import ModelType
    from app.providers.base import TTSOpts
    from app.providers.registry import ProviderRegistry
    from app.services.keyframe_service import _resolve_model
    from app.tasks.base import bytes_to_local

    model = _resolve_model(db, None, ModelType.tts, "voice")
    provider = ProviderRegistry.for_model(model)
    opts = TTSOpts(voice=voice_id or "default", emotion=emotion, instruct_text=None)
    handle = provider.synthesize(text, voice_id or "default", opts)
    return bytes_to_local(
        handle.meta["audio_bytes_b64"],
        subdir="voicechange/_src", filename=f"{uuid.uuid4().hex}.{handle.meta.get('format','mp3')}",
    )


def voice_change_video(video_url: str, audio_url: str, out_name: str = "") -> str:
    """把指定音频替换到视频音轨（视频轨 copy，音频重编码）。"""
    src = url_to_local(video_url)
    aud = url_to_local(audio_url)
    if not os.path.exists(src):
        raise ValueError("源视频文件不存在")
    if not os.path.exists(aud):
        raise ValueError("配音文件不存在")
    out_dir = os.path.join(settings.media_dir, "voicechange", str(uuid.uuid4()))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name or "voiced.mp4")
    _run(["ffmpeg", "-y", "-i", src, "-i", aud,
          "-map", "0:v", "-map", "1:a", "-c:v", "copy",
          "-c:a", "aac", "-shortest", out_path])
    return f"{settings.static_base_url}/media/voicechange/{os.path.basename(out_dir)}/{os.path.basename(out_path)}"


def find_draw_to_video_model(db):
    """探测配置里是否声明了支持 draw-to-video（草图局部重绘）能力的视频模型。

    返回 (Model, workflow_name) 或 (None, None)。
    """
    from app.models.model_config import Model, ModelType

    rows = db.query(Model).filter(
        Model.model_type == ModelType.video, Model.is_enabled.is_(True)
    ).all()
    for m in rows:
        cap = (getattr(m, "capability", None) or {})
        if cap.get("draw_to_video"):
            return m, cap["draw_to_video"]
    return None, None
