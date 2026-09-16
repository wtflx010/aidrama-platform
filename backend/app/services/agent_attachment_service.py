"""多模态附件处理服务（P8 Phase 5，2026-08-11）。

把用户上传的各类附件统一「消化」为可注入对话的形态：
- 图片（png/jpg/webp/gif）→ 保留 URL（视觉理解 + 生图参考）
- 音频（mp3/wav/m4a/ogg/flac）→ 本地 Whisper 转写为文字（ASR）
- 视频（mp4/mov/webm）→ ffmpeg 抽 3 帧关键帧（视觉理解）+ 保留 URL
- 文档（pdf/docx/txt/md/json）→ 提取纯文本（pypdf / zipfile / 直接读）

所有文件落盘到 media/agent_uploads/<session>/，返回结构化结果 dict，
由 POST /agent/attachments 接口处理后随 chat 请求 attachments 字段注入上下文。
"""
import base64
import logging
import os
import re
import subprocess
import uuid
import zipfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# 支持的文件类型 → 类型分组
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_DOC_EXTS = {".pdf", ".docx", ".txt", ".md", ".json", ".csv"}
_ALL_EXTS = _IMAGE_EXTS | _AUDIO_EXTS | _VIDEO_EXTS | _DOC_EXTS

# 音频转写：复用 CosyVoice 环境的 Whisper（模型已随该环境安装）
_WHISPER_PY = os.getenv(
    "COSYVOICE_PY",
    "~/miniconda3/envs/cosyvoice/bin/python",
)
_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# 附件大小上限（50MB，base64 后约 67MB）
_MAX_BYTES = 50 * 1024 * 1024

# session_id 白名单：仅允许安全字符（UUID 或 "preview"），防止路径穿越写穿 media_dir
_SESSION_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def process_attachment(filename: str, data_base64: str, session_id: str) -> dict:
    """保存附件并按类型处理，返回结构化结果 dict。失败抛 ValueError（前端转中文）。"""
    # 路径穿越防护：session_id 直接拼入落盘子目录，必须为安全字符，禁止 ../ 等
    if not _SESSION_RE.fullmatch(session_id or ""):
        raise ValueError("session_id 非法（仅允许字母/数字/下划线/连字符）")
    name = (filename or "attachment").strip() or "attachment"
    ext = Path(name).suffix.lower()
    if ext not in _ALL_EXTS:
        raise ValueError(
            f"不支持的文件类型「{ext}」。支持：图片/音频/视频/PDF/Word/文本",
        )
    try:
        raw = base64.b64decode(data_base64)
    except Exception:
        raise ValueError("附件数据解码失败")
    if not raw:
        raise ValueError("附件内容为空")
    if len(raw) > _MAX_BYTES:
        raise ValueError("附件过大（最大 50MB）")

    # 落盘到 media/agent_uploads/<session>/
    subdir = f"agent_uploads/{session_id}"
    local_dir = Path(settings.media_dir) / subdir
    local_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex[:8]}{ext}"
    local_path = local_dir / stored_name
    local_path.write_bytes(raw)
    url = f"{settings.static_base_url}/media/{subdir}/{stored_name}"

    base = {"name": name, "url": url}

    if ext in _IMAGE_EXTS:
        return {**base, "kind": "image"}
    if ext in _AUDIO_EXTS:
        transcript = _transcribe(local_path)
        return {**base, "kind": "audio", "transcript": transcript}
    if ext in _VIDEO_EXTS:
        frames = _extract_frames(local_path, subdir)
        return {**base, "kind": "video", "frames": frames}
    if ext == ".pdf":
        return {**base, "kind": "document", "text": _pdf_text(local_path)}
    if ext == ".docx":
        return {**base, "kind": "document", "text": _docx_text(local_path)}
    # 纯文本类
    try:
        text = local_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ValueError(f"读取文本失败：{e}")
    if len(text) > 120_000:
        text = text[:120_000] + "\n…（内容过长已截断）"
    return {**base, "kind": "document", "text": text}


def _transcribe(audio_path: Path) -> str:
    """本地 Whisper 转写音频 → 文字。失败返回错误占位文本（不阻断对话）。"""
    out_dir = "/tmp/agent_asr"
    os.makedirs(out_dir, exist_ok=True)
    try:
        r = subprocess.run(
            [
                _WHISPER_PY, "-m", "whisper", str(audio_path),
                "--model", _WHISPER_MODEL, "--language", "zh",
                "--output_format", "txt", "--output_dir", out_dir,
                "--fp16", "False", "--verbose", "False",
            ],
            capture_output=True, text=True, timeout=600,
        )
        # whisper 转写文本写入 <output_dir>/<音频basename>.txt（基于音频文件名，与输出目录无关）
        out_txt = Path(out_dir) / f"{audio_path.stem}.txt"
        if out_txt.exists():
            text = out_txt.read_text(encoding="utf-8", errors="replace").strip()
            out_txt.unlink(missing_ok=True)
            if text:
                return text
        # 兜底：stdout 里可能直接有转写文本（剔除进度条行）
        if r.stdout and r.stdout.strip():
            lines = [l for l in r.stdout.splitlines() if "%|" not in l and "/" not in l]
            clean = "\n".join(lines).strip()
            if clean:
                return clean[:120_000]
        return f"（音频转写失败：{r.stderr.strip()[-200:] or '无输出'}）"
    except subprocess.TimeoutExpired:
        return "（音频转写超时：音频过长，仅保留文件供参考）"
    except Exception as e:  # noqa: BLE001 - 转写失败不阻断对话
        return f"（音频转写失败：{e}）"


def _extract_frames(video_path: Path, subdir: str) -> list[str]:
    """ffmpeg 抽 3 帧（首/中/尾）→ 落盘 jpg，返回 URL 列表。失败返回空列表。"""
    frames: list[str] = []
    try:
        # 探测时长
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        duration = float(probe.stdout.strip() or 0)
        points = {0.0, duration / 2 if duration > 0 else 0.0, max(0.0, duration - 0.1)}
        for i, t in enumerate(sorted(points)):
            frame_name = f"frame_{uuid.uuid4().hex[:6]}_{i}.jpg"
            frame_path = Path(settings.media_dir) / subdir / frame_name
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "3", str(frame_path)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and frame_path.exists():
                frames.append(f"{settings.static_base_url}/media/{subdir}/{frame_name}")
    except Exception as e:  # noqa: BLE001
        logger.warning("视频抽帧失败: %s", e)
    return frames


def _pdf_text(pdf_path: Path) -> str:
    """pypdf 提取 PDF 文本。失败回退为错误提示。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        parts: list[str] = []
        for page in reader.pages[:100]:
            t = (page.extract_text() or "").strip()
            if t:
                parts.append(t)
        text = "\n".join(parts).strip()
        if not text:
            return "（PDF 为扫描件/无文本层，无法直接提取文字）"
        if len(text) > 120_000:
            text = text[:120_000] + "\n…（内容过长已截断）"
        return text
    except Exception as e:  # noqa: BLE001
        return f"（PDF 文本提取失败：{e}）"


def _docx_text(docx_path: Path) -> str:
    """docx 本质是 zip，直接解析 document.xml 提取文本（无需 python-docx）。"""
    try:
        with zipfile.ZipFile(docx_path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        # 段落 <w:p> 换行，run <w:t> 取文本
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
        lines: list[str] = []
        for p in paras:
            texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)
            line = "".join(texts)
            # 去 XML 转义
            line = (line.replace("&amp;", "&").replace("&lt;", "<")
                        .replace("&gt;", ">").replace("&quot;", '"')
                        .replace("&#39;", "'"))
            if line.strip():
                lines.append(line.strip())
        text = "\n".join(lines).strip()
        if not text:
            return "（Word 文档无可提取文本）"
        if len(text) > 120_000:
            text = text[:120_000] + "\n…（内容过长已截断）"
        return text
    except Exception as e:  # noqa: BLE001
        return f"（Word 文本提取失败：{e}）"
