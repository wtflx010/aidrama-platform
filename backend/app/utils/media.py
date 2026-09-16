"""媒体 URL 工具：把本地 media URL 转为 base64 data URI。

Agnes img2img / 视频接口不接受 localhost 或私有网络 URL（实测报错：
"port 8000 is not allowed" / "Localhost and private network URLs are not
supported"），需把本地图片转 base64 data URI 再传。公网 URL 原样返回。

踩坑：2K PNG 关键帧 ~7MB，base64 后 ~9MB，上传到 Agnes 时 Cloudflare
网关 100s 超时 → 504。PNG→JPEG(q85) 压缩至 ~1.5MB，彻底解决。
"""
import base64
import io
import logging
import math
import os
import struct
import subprocess

from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_MARKER = "/static/media/"
_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

# sRGB ICC profile 字节（惰性加载）。
# 2026-08-10 新增：所有 PIL 重存的 PNG 必须带 sRGB 色彩配置，否则在 macOS
# 广色域屏上浏览器按显示器色域渲染（偏艳/偏亮），与本地预览（按 sRGB）
# 色彩不一致——同一文件两端显示"色彩不同"（用户反馈）。
# 2026-08-10 修复：ImageCms.createProfile("sRGB") 只生成 588 字节的简化版
# "sRGB built-in" profile，Chrome 对简化 profile 解析不可靠（仍按显示器
# 色域渲染 → 曝光依旧）。改用系统标准 sRGB IEC61966-2.1 profile
# （3144 字节，/System/Library/ColorSync/Profiles/sRGB Profile.icc），
# Chrome/Safari/Preview 均按同一 sRGB 色彩空间渲染。
_srgb_icc_bytes: bytes | None = None

# 候选标准 sRGB profile 路径（按优先级）。首项为 macOS 系统标准 profile。
_SRGB_ICC_CANDIDATES = (
    "/System/Library/ColorSync/Profiles/sRGB Profile.icc",
    "/System/Library/ColorSync/Profiles/sRGB IEC61966-2.1.icc",
    "/Library/ColorSync/Profiles/sRGB Profile.icc",
)


def _get_srgb_icc() -> bytes:
    """返回标准 sRGB ICC profile 字节；全部不可用时返回空（不带 profile）。

    优先读取系统标准 sRGB IEC61966-2.1 profile（3144 字节，全平台浏览器
    与预览器均识别）。读取失败时回退 PIL ImageCms 生成的 profile（588 字节，
    兼容性较差但聊胜于无）。
    """
    global _srgb_icc_bytes
    if _srgb_icc_bytes is not None:
        return _srgb_icc_bytes
    _srgb_icc_bytes = b""
    # 1) 系统标准 sRGB profile
    for cand in _SRGB_ICC_CANDIDATES:
        try:
            if os.path.isfile(cand):
                with open(cand, "rb") as f:
                    data = f.read()
                # 校验 ICC 有效性：offset 36 处的 "acsp" 签名（完整 sRGB 约 3KB）
                if len(data) >= 128 and data[36:40] == b"acsp":
                    _srgb_icc_bytes = data
                    logger.info("[media] 使用系统标准 sRGB profile: %s (%d 字节)", cand, len(data))
                    return _srgb_icc_bytes
        except OSError:
            continue
    # 2) PIL 兜底（588 字节简化 profile）
    try:
        from PIL import ImageCms

        _srgb_icc_bytes = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        ).tobytes()
    except Exception:
        _srgb_icc_bytes = b""
    return _srgb_icc_bytes


def save_png_srgb(img: Image.Image, path: str) -> None:
    """以带 sRGB ICC profile 的 PNG 保存图片（修复广色域屏色彩不一致）。

    所有经过 PIL 重存的 PNG（flood-fill 染底、过曝压暗、四视图拼接、网格
    拼接等）统一走本函数：写入标准 sRGB IEC61966-2.1 iCCP chunk，浏览器
    与本地预览器按同一 sRGB 色彩空间渲染，消除系统端与服务器端色彩差异。
    """
    icc = _get_srgb_icc()
    if icc:
        img.save(path, "PNG", icc_profile=icc)
    else:
        img.save(path, "PNG")


def clean_remote_url(url: str | None) -> str | None:
    """清洗远端 URL：去首尾空白与反引号。

    Agnes 偶发返回 `` `https://...` ``（markdown 残留反引号包裹）的形式，
    原样传给 curl 会被拒绝（URL rejected）或解析异常。所有远端 URL 在下载/
    入库前统一清洗。
    """
    if not url:
        return url
    return url.strip().strip("`")


def delete_media_file(url: str | None) -> None:
    """删除本地媒体文件（视频/关键帧/成片），公网 URL 与缺失文件静默忽略。

    支持的 URL 形态（本地 media/export）：
      {static_base_url}/media/...      → {media_dir}/...
      {static_base_url}/exports/...    → {export_dir}/...
    删除文件后尝试清理空父目录（如 videos/{clip_id}/、keyframes/{kf.id}/），
    成片目录 exports/{project_id}/ 为空时同样清理（下次导出会自动重建）。

    全程打详细日志：URL→本地路径解析、文件存在性、删除结果、空目录清理、
    删除后残留预警，便于排查文件残留问题。
    """
    if not url:
        logger.info("[media] delete_media_file 跳过（URL 为空）")
        return
    marker = "/static/"
    if marker not in url:
        logger.info("[media] delete_media_file 跳过公网 URL（无本地文件）: %s", url[:160])
        return
    rel = url.split(marker, 1)[1]  # 如 media/videos/{id}/clip.mp4
    if rel.startswith("media/"):
        local = os.path.join(settings.media_dir, rel[len("media/"):])
    elif rel.startswith("exports/"):
        local = os.path.join(settings.export_dir, rel[len("exports/"):])
    else:
        logger.warning("[media] delete_media_file 无法识别的本地 URL 前缀: %s", url[:160])
        return

    exists = os.path.isfile(local)
    logger.info(
        "[media] delete_media_file url=%s -> local=%s exists=%s",
        url, local, exists,
    )
    if not exists:
        logger.info("[media] 文件不存在，无需删除: %s", local)
        return
    try:
        size = os.path.getsize(local)
        os.remove(local)
    except OSError as e:
        logger.warning("[media] 删除文件失败 %s: %s", local, e)
        return
    logger.info("[media] 已删除文件 %s (%.1f KB)", local, size / 1024.0)
    # 残留预警：删除后文件仍存在说明删除不彻底（如权限/并发写入）
    if os.path.exists(local):
        logger.warning("[media] 文件删除后仍存在（残留预警）: %s", local)
    parent = os.path.dirname(local)
    try:
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
            logger.info("[media] 空父目录已清理: %s", parent)
    except OSError as e:
        logger.warning("[media] 空父目录清理失败 %s: %s", parent, e)


def media_url_to_data_uri(url: str | None) -> str | None:
    """把本地 media URL 转为 base64 data URI；公网 URL 原样返回。

    - url 为空 → 返回 None/空
    - url 含 /static/media/ → 解析本地路径，读文件转 data URI
    - 其他（公网 https URL）→ 原样返回
    - 本地文件不存在 → 原样返回 url（交由上游报错，便于定位）
    - PNG 自动转 JPEG(q85) 压缩，避免大 payload 触发 Cloudflare 504
    """
    if not url:
        return url
    if _MARKER not in url:
        return url  # 公网 URL
    local_path = os.path.join(settings.media_dir, url.split(_MARKER, 1)[1])
    if not os.path.exists(local_path):
        return url
    ext = os.path.splitext(local_path)[1].lower().lstrip(".")

    # PNG → JPEG 压缩：7MB PNG → ~1.5MB JPEG，base64 后 ~2MB，避免 504
    if ext == "png":
        try:
            img = Image.open(local_path)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return f"data:image/jpeg;base64,{b64}"
        except Exception:
            pass  # PIL 转换失败则回退到原始读取

    mime = _MIME.get(ext, "image/png")
    with open(local_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _detect_burst(local_path: str, window_sec: float = 0.8) -> tuple[float, float] | None:
    """检测视频开头音频的瞬态爆音段 [start, end]（秒），无则返回 None。

    2026-08-10 迭代3：不同视频爆音位置不同（实测 d9c4cf7c 在 0.09~0.20s，
    462201cd 在 0~0.15s）。固定静音窗口只能覆盖一部分。改为检测：
    - 把开头 window_sec 切成 5ms 窗口算 RMS；
    - 找所有连续 > -35dB 的段；
    - 瞬态判定 = 段长 < 0.25s（爆音短促）且能量包络先升后降（peak-last ≥ 6dB，
      快速衰减）；持续内容（语音/音乐，段长 > 0.25s）不会被误判。
    返回时各外扩 20ms 余量，保证爆音首尾都被切干净。
    """
    SR = 48000
    raw = f"{local_path}.scan.f32"
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", local_path, "-vn", "-ac", "1", "-ar", str(SR),
             "-f", "f32le", "-t", str(window_sec), "-y", raw],
            capture_output=True,
        )
        if r.returncode != 0 or not os.path.isfile(raw):
            return None
        data = open(raw, "rb").read()
        samples = struct.unpack(f"<{len(data) // 4}f", data)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass

    win = int(SR * 0.005)  # 5ms
    dbs: list[tuple[float, float]] = []
    for i in range(0, min(len(samples), int(SR * window_sec)), win):
        seg = samples[i:i + win]
        rms = (sum(x * x for x in seg) / len(seg)) ** 0.5 if seg else 0
        dbs.append((i / SR, 20 * math.log10(rms) if rms > 1e-6 else -120))

    threshold = -35.0
    segs: list[tuple[float, float, float, float]] = []  # (s, e, peak, last)
    cur = None
    for t, db in dbs:
        if db > threshold:
            if cur is None:
                cur = [t, t, db, db]
            cur[1] = t
            cur[2] = max(cur[2], db)
            cur[3] = db
        elif cur is not None:
            segs.append(tuple(cur))
            cur = None
    if cur is not None:
        segs.append(tuple(cur))

    for s, e, peak, last in segs:
        if s >= 0.4:
            break  # H3 爆音只出现在极靠前（<0.3s）；0.4s 后的高能段是内容
        if e - s > 0.25:
            continue  # 持续内容（语音/音乐），不是瞬态爆音
        if peak - last >= 6:
            return (max(0.0, s - 0.02), min(e + 0.02, window_sec))
    return None


def apply_audio_fade_in(local_path: str, mute_sec: float = 0.08, fade_sec: float = 0.22) -> bool:
    """对视频音频开头的瞬态爆音做「精准静音 + 平滑淡入」。

    2026-08-10 迭代3：H3 爆音位置随视频变化（0~0.2s 不等）。先用
    _detect_burst 动态定位瞬态段，直接静音该段（爆音完全切除、不伤后续
    内容），段尾加 30ms 平滑淡入防台阶；检测不到爆音时按保守静音
    mute_sec + 淡入 fade_sec 处理（幂等）。仅重编码音频（-c:v copy）。

    - 无音轨 / ffmpeg 缺失 / 处理失败时静默跳过（不阻断生成流程）
    - 原地覆盖保存（临时文件 + rename）
    返回是否处理成功。
    """
    if not os.path.isfile(local_path):
        return False
    tmp = f"{local_path}.fade_tmp.mp4"
    try:
        burst = _detect_burst(local_path)
        if burst:
            s, e = burst
            # 精准静音瞬态段 + 段尾 30ms 平滑淡入（qsin 从 0 起，无台阶）
            af = (
                f"volume=0:enable='between(t,{s:.3f},{e:.3f})',"
                f"afade=t=in:st={e:.3f}:d=0.03:curve=qsin"
            )
            logger.info("[media] 检测到开头瞬态爆音 %.3f~%.3fs，已静音: %s", s, e, local_path)
        else:
            af = (
                f"volume=0:enable='lt(t,{mute_sec})',"
                f"afade=t=in:st={mute_sec}:d={fade_sec}:curve=qsin"
            )
        cmd = [
            "ffmpeg", "-y", "-i", local_path,
            "-af", af,
            "-c:v", "copy", "-c:a", "aac",
            "-loglevel", "error", tmp,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0 or not os.path.isfile(tmp):
            logger.warning("[media] 音频淡入处理失败，跳过: %s", proc.stderr[-300:])
            return False
        os.replace(tmp, local_path)
        logger.info("[media] 已对视频开头 %ss 音频淡入: %s", fade_sec, local_path)
        return True
    except Exception as e:  # noqa: BLE001 - 后处理失败不应阻断视频生成
        logger.warning("[media] 音频淡入异常，跳过: %s", e)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False

def crop_grid_cell(
    media_url: str,
    grid: tuple[int, int],
    index: int,
    out_prefix: str = "view",
) -> str | None:
    """从多视图网格图按 (cols, rows) 等分裁剪第 index 格，返回新生成图的本地 media URL。

    用于图生视频参考：场景多视图（1344x768 = 3x2，每格 448x384）与角色四视图
    （1536x1024 = 2x2，每格 768x512）都是规整网格，按列优先顺序裁剪：
      index = col + row * cols（第 0 格 = 左上）。
    产物落盘 {media_dir}/views/{asset_id}/scene_sheet__{prefix}_{index}.png，
    URL 为 {origin}/static/media/views/{asset_id}/... 。非本地 URL / 裁剪失败返回 None。
    """
    marker = "/static/"
    if not media_url or marker not in media_url:
        logger.warning("[media] crop_grid_cell 跳过非本地 URL: %s", (media_url or "")[:120])
        return None
    try:
        rel = media_url.split(marker, 1)[1]
        if not rel.startswith("media/assets/"):
            logger.warning("[media] crop_grid_cell 仅支持 media/assets 路径: %s", media_url[:120])
            return None
        local = os.path.join(settings.media_dir, rel[len("media/"):])
        if not os.path.isfile(local):
            logger.warning("[media] crop_grid_cell 源文件不存在: %s", local)
            return None

        from PIL import Image as _PILImage
        img = _PILImage.open(local)
        w, h = img.size
        cols, rows = grid
        if cols <= 0 or rows <= 0 or cols * rows <= index:
            logger.warning("[media] crop_grid_cell 网格参数非法: grid=%s index=%s size=%s", grid, index, img.size)
            return None
        cw, ch = w // cols, h // rows
        c, r = index % cols, index // cols
        cell = img.convert("RGB").crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))

        asset_id = os.path.basename(os.path.normpath(os.path.dirname(local)))
        out_dir = os.path.join(settings.media_dir, "views", asset_id)
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(local))[0]
        out_name = f"{base}__{out_prefix}_{index}.png"
        out_path = os.path.join(out_dir, out_name)
        cell.save(out_path, "PNG")

        origin = media_url.split("/static/media/", 1)[0]
        out_url = f"{origin}/static/media/views/{asset_id}/{out_name}"
        logger.info(
            "[media] crop_grid_cell grid=%s index=%s -> %s (%sx%s)",
            grid, index, out_url, cell.width, cell.height,
        )
        return out_url
    except Exception as e:  # noqa: BLE001
        logger.warning("[media] crop_grid_cell 裁剪失败 %s: %s", media_url[:120], e)
        return None
