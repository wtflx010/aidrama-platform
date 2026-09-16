"""成片导出任务：单镜合成（视频 + 多条配音拼接，tpad/apad 对齐）→ concat 拼接
→ 混音 BGM + SFX → 字幕硬烧（Pillow + ffmpeg overlay）或软字幕回退。

P3 改造：支持单镜多条 VoiceLine（对白+旁白）按顺序拼接为完整音轨，视频时长
按音频总时长智能对齐（视频短则 tpad clone 末帧，视频长则保留完整画面 + apad 静音补齐）。

字幕说明：优先用 Pillow 渲染字幕 PNG + ffmpeg overlay 硬烧到视频画面（不依赖
libass/drawtext），任何播放器都能直接看到。硬烧失败时回退 mov_text 软字幕轨。

混音说明：
- BGM：每个 Episode 一条 BgmTrack，按 episode 在成片中的起始时间偏移，
  循环播放至 episode 结束，volume 默认 0.15。
- SFX：每个分镜可有多条 SfxClip，按 segment 全局起始 + clip.start_time 偏移，
  volume 默认 0.5。
- 混音用 amix normalize=0 保留各路原始音量，避免 voice 被 BGM 稀释。
"""
import logging
import os
import shutil
import subprocess
import tempfile

from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.config import settings
from app.database import SessionLocal
from app.models.bgm import BgmTrack
from app.models.media import MediaStatus
from app.models.segment import Segment
from app.models.sfx import SfxClip
from app.models.task import Task, TaskStatus
from app.models.voice import Subtitle, VoiceLine
from app.providers.errors import map_to_chinese
from app.tasks.base import now, update_task
from app.tasks.celery_app import celery_app

# ffmpeg/ffprobe 子进程超时（秒）：合成/混音/字幕烧录属长时 CPU 任务，
# 无显式超时会卡死 worker；1800s 与视频/幕级长任务上限对齐。
_FFMPEG_TIMEOUT = 1800

# P5 九宫格运镜：转场时长自适应规则（按相邻景别关系与情绪选 xfade 时长）
_SHOT_LEVEL = {"远景": 0, "全景": 1, "中景": 2, "近景": 3, "特写": 4}
# 紧张情绪 → 快切（0.2s 近硬切），增强冲击力
_TENSE_EMOTIONS = {"愤怒", "紧张", "恐惧", "震惊"}
# 舒缓收束情绪 → 慢叠化（0.6s 淡入淡出）
_SOFT_EMOTIONS = {"温馨", "平静", "悲伤"}


def _transition_fade_secs(prev_seg: Segment | None, next_seg: Segment | None, is_ep_boundary: bool) -> float:
    """相邻镜转场时长：幕间 0.6s；后镜情绪紧张 0.2s 近硬切；
    景别向前跳级（后镜更近，级别 >= 前镜+2，如中景→特写）0.2s 硬切冲击；
    后镜情绪舒缓（温馨/平静/悲伤）0.6s 慢叠化；
    同级/相邻级连续叙事 0.3s 叠化。

    注：向后回拉（如特写→中景）是情绪释放/舒缓，不按跳级硬切。"""
    if is_ep_boundary:
        return 0.6
    next_emotion = (next_seg.emotion or "").strip() if next_seg else ""
    if next_emotion in _TENSE_EMOTIONS:
        return 0.2
    p_lv = _SHOT_LEVEL.get(prev_seg.shot_type) if prev_seg else None
    c_lv = _SHOT_LEVEL.get(next_seg.shot_type) if next_seg else None
    if p_lv is not None and c_lv is not None and c_lv - p_lv >= 2:
        return 0.2
    if next_emotion in _SOFT_EMOTIONS:
        return 0.6
    return 0.3


def url_to_local_path(url: str) -> str | None:
    """将对外可访问的 media URL 还原为本地磁盘路径。

    形如 http://localhost:8000/static/media/videos/{cid}/clip.mp4
    → {media_dir}/videos/{cid}/clip.mp4
    """
    marker = "/static/media/"
    if url and marker in url:
        return os.path.join(settings.media_dir, url.split(marker, 1)[1])
    return None


def ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
        timeout=_FFMPEG_TIMEOUT,
    )
    try:
        return float(out.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def ffprobe_has_audio(path: str) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=False,
        timeout=_FFMPEG_TIMEOUT,
    )
    return "audio" in out.stdout


def _ts(ms: int) -> str:
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, mm = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{mm:03d}"


def write_srt(subs: list[tuple[int, int, str]], path: str) -> bool:
    """写入 SRT；subs 为 (start_ms, end_ms, text) 列表。空则返回 False。"""
    if not subs:
        return False
    lines: list[str] = []
    for i, (start, end, text) in enumerate(subs, 1):
        lines.append(str(i))
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(text or "")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


def compose_segment(
    video_path: str,
    voice_path: str | None,
    out_path: str,
    *,
    include_voice: bool,
    target_w: int | None = None,
    target_h: int | None = None,
    fps: int | None = None,
) -> float:
    """单镜合成：tpad/apad 对齐时长 + 配音替换音轨，输出 H.264/AAC mp4。

    返回合成后片段时长（秒），供全局字幕时间轴偏移使用。

    target_w/target_h：统一分辨率（scale+pad 黑边，保持宽高比，兜底画幅不一致）
    fps：统一帧率（兜底帧率不一致）
    """
    v_dur = ffprobe_duration(video_path)
    use_voice = include_voice and bool(voice_path) and os.path.exists(voice_path)
    voice_dur = ffprobe_duration(voice_path) if use_voice else None
    target = max(v_dur, voice_dur or 0.0)
    if target <= 0:
        raise ValueError(f"无法探测视频时长：{video_path}")

    v_pad = max(0.0, target - v_dur)

    # 视频链：[0:v] 输入 pad 直接接首滤镜（无逗号），滤镜间用逗号分隔，[v] 接末滤镜
    vf = "[0:v]"
    if v_pad > 0.001:
        vf += f"tpad=stop_mode=clone:stop_duration={v_pad:.3f},"
    if target_w and target_h:
        # 统一规格：等比缩放 + 黑边补齐，避免不同画幅的分镜拼接时画面突变
        vf += (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
        )
    else:
        vf += "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
    if fps:
        vf += f"fps={fps},"
    vf += "format=yuv420p[v]"

    inputs = ["-i", video_path]
    if use_voice:
        inputs += ["-i", voice_path]

    filters = [vf]
    if use_voice:
        filters.append(f"[1:a]aresample=44100,apad=whole_dur={target:.3f}[a]")
    elif ffprobe_has_audio(video_path):
        filters.append(f"[0:a]aresample=44100,apad=whole_dur={target:.3f}[a]")
    else:
        filters.append(f"anullsrc=r=44100:cl=stereo:duration={target:.3f}[a]")

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-pix_fmt", "yuv420p",  # 强制 4:2:0，确保安卓/微信/剪映可播
        "-c:v", "libx264", "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    return target


def _concat_audio(voice_paths: list[str], out_path: str) -> float:
    """拼接多条音频为单个 wav，返回总时长（秒）。

    用 ffmpeg concat demuxer 重编码为统一 wav（44100Hz stereo），避免不同
    VoiceLine 格式/采样率不一致导致拼接失败。
    """
    fd, list_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            for p in voice_paths:
                safe = p.replace("'", r"'\''")
                f.write(f"file '{safe}'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-ar", "44100", "-ac", "2", out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    finally:
        if os.path.exists(list_path):
            os.unlink(list_path)
    return ffprobe_duration(out_path)


def compose_segment_multi(
    video_path: str,
    voice_paths: list[str],
    out_path: str,
    *,
    include_voice: bool,
    tmpdir: str,
    target_w: int | None = None,
    target_h: int | None = None,
    fps: int | None = None,
) -> float:
    """单镜合成（多对白版）：多条 VoiceLine 拼接后与视频对齐，输出 H.264/AAC mp4。

    P3：替代 compose_segment，支持单镜多条对白 VoiceLine 按顺序拼接为完整音轨。
    - voice_paths 为空 → 无配音，视频原音轨/静音对齐
    - voice_paths 单条 → 直接用 compose_segment
    - voice_paths 多条 → 先 _concat_audio 拼接，再按单条逻辑合成

    时长对齐（智能）：
    - target = max(v_dur, voice_total_dur)
    - 视频短于音频：tpad clone 末帧补齐
    - 视频长于音频：apad 静音补齐（保留完整视频画面）

    返回合成后片段时长（秒）。
    """
    if not include_voice or not voice_paths:
        return compose_segment(
            video_path, None, out_path, include_voice=False,
            target_w=target_w, target_h=target_h, fps=fps,
        )

    # 过滤不存在的文件
    valid_paths = [p for p in voice_paths if p and os.path.exists(p)]
    if not valid_paths:
        return compose_segment(
            video_path, None, out_path, include_voice=False,
            target_w=target_w, target_h=target_h, fps=fps,
        )

    if len(valid_paths) == 1:
        return compose_segment(
            video_path, valid_paths[0], out_path, include_voice=True,
            target_w=target_w, target_h=target_h, fps=fps,
        )

    # 多条：先拼接为单个音频文件
    merged_voice = os.path.join(tmpdir, f"merged_voice_{os.path.basename(out_path)}.wav")
    _concat_audio(valid_paths, merged_voice)
    return compose_segment(
        video_path, merged_voice, out_path, include_voice=True,
        target_w=target_w, target_h=target_h, fps=fps,
    )


_SUB_PREFIXES = ("旁白：", "旁白:", "叙述者：", "叙述者:", "画外音：", "画外音:", "【旁白】", "（旁白）", "(旁白)", "旁白")


def _strip_sub_prefix(speaker: str, text: str, *, is_narration: bool = False) -> str:
    """字幕文案去除说话人/旁白前缀（2026-09-02：字幕只显示台词/旁白正文，不带"名字：""旁白："字样）。

    分镜存储的 dialogue 可能是"小云：嗨，你来啦。"（带名字）；旁白也可能是"旁白：……"。
    这里统一剥掉开头的人名/叙述者前缀，仅保留正文。
    """
    t = (text or "").strip()
    for p in _SUB_PREFIXES:
        if t.startswith(p):
            t = t[len(p):].strip()
            break
    if not is_narration and speaker:
        for pre in (f"{speaker}：", f"{speaker}:", f"{speaker} ", f"{speaker}"):
            if t.startswith(pre):
                t = t[len(pre):].strip()
                break
    return t


def concat_clips(local_paths: list[str], out_path: str) -> None:
    """concat demuxer 拼接，重编码保证兼容性。"""
    fd, list_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            for p in local_paths:
                safe = p.replace("'", r"'\''")
                f.write(f"file '{safe}'\n")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-pix_fmt", "yuv420p",  # 强制 4:2:0，确保安卓/微信/剪映可播
            "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    finally:
        if os.path.exists(list_path):
            os.unlink(list_path)


def concat_clips_xfade(local_paths: list[str], out_path: str, fade_secs: list[float]) -> float:
    """xfade 无缝转场拼接：相邻片段交叉淡化（video xfade + audio acrossfade）。

    fade_secs[i] = 第 i 段与第 i+1 段之间的转场时长（len = n-1）。
    总时长 = sum(dur_i) - sum(fade_secs)（转场重叠段被缩短）。
    返回成片总时长（秒）。

    要求：所有片段分辨率/帧率一致（由合成阶段统一规格保证）。
    """
    n = len(local_paths)
    if n == 1:
        shutil.copyfile(local_paths[0], out_path)
        return ffprobe_duration(local_paths[0])
    assert len(fade_secs) == n - 1, "fade_secs 数量须为片段数-1"

    durs = [ffprobe_duration(p) for p in local_paths]
    inputs: list[str] = []
    for p in local_paths:
        inputs += ["-i", p]

    parts: list[str] = []
    # 视频 xfade 链：第 i 段与前一输出转场，offset = 前 i 段累计展示时长 - 转场时长
    prev_v = "0:v"
    cum = durs[0]
    for i in range(1, n):
        fade = fade_secs[i - 1]
        offset = cum - fade
        label = f"v{i}" if i < n - 1 else "vout"
        parts.append(
            f"[{prev_v}][{i}:v]xfade=transition=fade:duration={fade:.3f}:"
            f"offset={offset:.3f}[{label}]"
        )
        prev_v = label
        cum = cum + durs[i] - fade

    # 音频 acrossfade 链：交叉淡化，与视频重叠时长一致
    prev_a = "0:a"
    for i in range(1, n):
        fade = fade_secs[i - 1]
        label = f"a{i}" if i < n - 1 else "aout"
        parts.append(
            f"[{prev_a}][{i}:a]acrossfade=d={fade:.3f}:c1=tri:c2=tri[{label}]"
        )
        prev_a = label

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "[aout]",
        # xfade 滤镜会把输出提升为 yuv444p，若不强制 yuv420p，libx264 会编成
        # H.264 High 4:4:4 → 安卓/鸿蒙/抖音/剪映硬解不支持 → 黑屏有声
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    return cum


def ffprobe_size(path: str) -> tuple[int, int]:
    """探测视频宽高（w, h）。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", path],
        capture_output=True, text=True, check=True,
        timeout=_FFMPEG_TIMEOUT,
    )
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


def apply_fade(
    in_path: str,
    out_path: str,
    *,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    fps: int | None = None,
) -> None:
    """对片段做淡入/淡出（视频黑场渐变 + 音频音量渐变），输出 H.264/AAC mp4。

    用于成片首尾淡入淡出、幕与幕之间的黑场过渡。fade 不改变片段总时长，
    因此全局字幕/BGM/SFX 时间轴无需调整；fade_out 后接 fade_in 即形成黑场闪断。
    """
    dur = ffprobe_duration(in_path)
    vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if fps:
        vf += f",fps={fps}"
    if fade_in > 0:
        vf += f",fade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        vf += f",fade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}"
    vf += ",format=yuv420p[v]"

    af = "[0:a]aresample=44100"
    if fade_in > 0:
        af += f",afade=t=in:st=0:d={fade_in:.3f}"
    if fade_out > 0:
        af += f",afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}"
    af += "[a]"

    cmd = [
        "ffmpeg", "-y",
        "-i", in_path,
        "-filter_complex", f"{vf};{af}",
        "-map", "[v]", "-map", "[a]",
        "-pix_fmt", "yuv420p",  # 强制 4:2:0，确保安卓/微信/剪映可播
        "-c:v", "libx264", "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)


def mix_bgm_sfx(
    concat_path: str,
    bgm_inputs: list[dict],
    sfx_inputs: list[dict],
    out_path: str,
) -> None:
    """在 concat.mp4 基础上混入 BGM 和 SFX 音轨。

    bgm_inputs: [{path, start_ms, volume, duration_ms}]  BGM 须循环至 duration_ms
    sfx_inputs: [{path, start_ms, volume}]
    无任何额外音轨时直接 copy。

    混音策略：amix normalize=0 保留各路音量，voice 在 [0:a] 原音量，
    BGM/SFX 各自按 volume 缩放后叠入。
    """
    extra_inputs = [b["path"] for b in bgm_inputs if os.path.exists(b["path"])] + \
                   [s["path"] for s in sfx_inputs if os.path.exists(s["path"])]
    if not extra_inputs:
        shutil.copyfile(concat_path, out_path)
        return

    # 构建 ffmpeg 命令：原视频为 -i 0，BGM/SFX 依次为 -i 1..N
    cmd = ["ffmpeg", "-y", "-i", concat_path]
    for p in extra_inputs:
        cmd += ["-i", p]

    filter_parts: list[str] = []
    mix_labels: list[str] = ["[0:a]"]
    idx = 1  # 输入索引从 1 开始（0 是 concat.mp4）

    # BGM：adelay + aloop + volume + 幕间淡入淡出（afade，避免幕切换时 BGM 硬切）
    for b in bgm_inputs:
        if not os.path.exists(b["path"]):
            continue
        start_ms = int(b["start_ms"])
        vol = float(b["volume"])
        # aloop 循环到目标时长（样本数太大用 2e9 近似无限循环后用 -t 限制）
        dur_ms = int(b.get("duration_ms", 0)) or 0
        loop_part = f",aloop=loop=-1:size=2e9" if dur_ms > 0 else ""
        # 幕间过渡：开头 0.5s 淡入、结尾 0.5s 淡出（幕时长过短时只保留淡入）
        fade_part = ",afade=t=in:st=0:d=0.5" if dur_ms > 500 else ""
        if dur_ms > 1500:
            fade_part += (
                f",afade=t=out:st={max(0.0, dur_ms / 1000 - 0.5):.3f}:d=0.5"
            )
        label = f"b{idx}"
        filter_parts.append(
            f"[{idx}:a]aresample=44100,adelay={start_ms}|{start_ms}{loop_part},"
            f"volume={vol}{fade_part}[{label}]"
        )
        mix_labels.append(f"[{label}]")
        idx += 1

    # SFX：adelay + volume
    for s in sfx_inputs:
        if not os.path.exists(s["path"]):
            continue
        start_ms = int(s["start_ms"])
        vol = float(s["volume"])
        label = f"s{idx}"
        filter_parts.append(
            f"[{idx}:a]aresample=44100,adelay={start_ms}|{start_ms},volume={vol}[{label}]"
        )
        mix_labels.append(f"[{label}]")
        idx += 1

    # amix：normalize=0 保留各路原始音量
    n = len(mix_labels)
    mix_str = "".join(mix_labels) + f"amix=inputs={n}:duration=first:normalize=0[aout]"
    filter_parts.append(mix_str)

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)


def mux_subtitles(video_path: str, srt_path: str, out_path: str) -> None:
    """把 SRT 作为 mov_text 软字幕轨嵌入 mp4（视频/音频流 copy）。"""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", srt_path,
        "-c", "copy",
        "-c:s", "mov_text",
        "-metadata:s:s:0", "language=chi",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)


def _render_subtitle_png(text: str, video_w: int, tmpdir: str) -> str:
    """用 Pillow 渲染单条字幕到透明 PNG（底部居中，黑底白字带描边）。

    不依赖 libass/drawtext，仅依赖 Pillow + 系统中文字体。
    """
    from PIL import Image, ImageDraw, ImageFont

    font_size = max(28, int(video_w * 0.035))  # 按视频宽度自适应
    # 尝试多个中文字体
    font_paths = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    font = None
    for fp in font_paths:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    # 计算文本尺寸
    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    bbox = tmp_draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # 文本超宽时自动换行
    max_w = int(video_w * 0.85)
    if text_w > max_w:
        # 按字符均分两行
        mid = len(text) // 2
        # 找最近的标点断句
        for offset in range(min(10, mid)):
            for pos in [mid + offset, mid - offset]:
                if 0 <= pos < len(text) and text[pos] in "，。！？；,;.?!":
                    mid = pos + 1
                    break
            else:
                continue
            break
        line1, line2 = text[:mid], text[mid:]
        return _render_subtitle_png_two_lines(line1, line2, video_w, font_size, font, tmpdir)

    # 单行
    padding_x, padding_y = 16, 8
    img_w = min(text_w + padding_x * 2, video_w)
    img_h = text_h + padding_y * 2
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 半透明黑底
    draw.rectangle([0, 0, img_w - 1, img_h - 1], fill=(0, 0, 0, 170))
    # 描边（四方向偏移1px画黑）
    x = (img_w - text_w) // 2
    y = padding_y
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 220))
    # 白色正文
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    fd, path = tempfile.mkstemp(suffix=".png", dir=tmpdir)
    os.close(fd)
    img.save(path)
    return path


def _render_subtitle_png_two_lines(
    line1: str, line2: str, video_w: int, font_size: int, font, tmpdir: str
) -> str:
    """渲染两行字幕。"""
    from PIL import Image, ImageDraw

    tmp_img = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    b1 = tmp_draw.textbbox((0, 0), line1, font=font)
    b2 = tmp_draw.textbbox((0, 0), line2, font=font)
    w1, w2 = b1[2] - b1[0], b2[2] - b2[0]
    h1, h2 = b1[3] - b1[1], b2[3] - b2[1]
    text_w = max(w1, w2)
    text_h = h1 + h2 + 6

    padding_x, padding_y = 16, 8
    img_w = min(text_w + padding_x * 2, video_w)
    img_h = text_h + padding_y * 2
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, img_w - 1, img_h - 1], fill=(0, 0, 0, 170))

    y = padding_y
    for line, lw in [(line1, w1), (line2, w2)]:
        x = (img_w - lw) // 2
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += h1 + 6

    fd, path = tempfile.mkstemp(suffix=".png", dir=tmpdir)
    os.close(fd)
    img.save(path)
    return path


def burn_subtitles(
    video_path: str, subs: list[tuple[int, int, str]], out_path: str, tmpdir: str
) -> bool:
    """用 Pillow 渲染字幕 PNG + ffmpeg overlay 硬烧到视频（不依赖 libass）。

    subs: [(start_ms, end_ms, text), ...]
    返回 True 表示成功硬烧；False 表示无字幕可烧。
    """
    if not subs:
        return False

    # 探测视频宽度
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, check=True,
        timeout=_FFMPEG_TIMEOUT,
    )
    video_w = int(probe.stdout.strip())

    # 渲染每条字幕的 PNG
    png_paths: list[tuple[str, float, float]] = []
    for start_ms, end_ms, text in subs:
        if not text or not text.strip():
            continue
        png = _render_subtitle_png(text.strip(), video_w, tmpdir)
        png_paths.append((png, start_ms / 1000.0, end_ms / 1000.0))

    if not png_paths:
        return False

    # 构建 ffmpeg overlay 命令
    cmd = ["ffmpeg", "-y", "-i", video_path]
    for png, _, _ in png_paths:
        cmd += ["-i", png]

    # overlay 链：每条字幕按时间区间叠加
    filter_parts: list[str] = []
    prev_label = "0:v"
    for i, (_, start, end) in enumerate(png_paths):
        label = f"v{i}" if i < len(png_paths) - 1 else "vout"
        # 底部居中，距底部 8% 高度
        filter_parts.append(
            f"[{prev_label}][{i + 1}:v]overlay="
            f"enable='between(t,{start:.3f},{end:.3f})':"
            f"x=(W-w)/2:y=H-h-H*0.08[{label}]"
        )
        prev_label = label

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", "0:a?",
        "-pix_fmt", "yuv420p",  # 强制 4:2:0，确保安卓/微信/剪映可播
        "-c:v", "libx264", "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_FFMPEG_TIMEOUT)

    # 清理 PNG
    for png, _, _ in png_paths:
        try:
            os.unlink(png)
        except OSError:
            pass
    return True


def _fallback_episode_video_subs(segs: list, total_dur_ms: int) -> list[tuple[int, int, str]]:
    """原生语音模式下无 TTS 配音时的字幕回退（幕级视频段）：按段内台词/旁白字数比例分配段时长。

    视频模型（Agnes）原生朗读台词，但没有 voiceline 行可用，字幕时间轴只能估算：
    每句台词按字数占比切分该段视频总时长（语速参考共享 CHARS_PER_SEC，加前后留白）。
    """
    if not segs or total_dur_ms <= 0:
        return []
    lines: list[tuple[str, str]] = []  # (speaker_or_旁白, text)
    for seg in segs:
        for dl in (seg.dialogue_lines or []):
            if isinstance(dl, dict):
                text = (dl.get("text") or "").strip()
                if text:
                    speaker = (dl.get("speaker") or "").strip()
                    lines.append((speaker, text))
        if seg.narration and seg.narration.strip():
            lines.append(("旁白", seg.narration.strip()))
    if not lines:
        return []
    total_chars = sum(len(text) for _, text in lines)
    if total_chars <= 0:
        return []
    subs: list[tuple[int, int, str]] = []
    cursor = 0
    for speaker, text in lines:
        span = max(1, int(total_dur_ms * len(text) / total_chars))
        end = min(total_dur_ms, cursor + span)
        label = _strip_sub_prefix(speaker, text)
        subs.append((cursor, end, label))
        cursor = end
    # 最后一句延伸到段尾，避免末尾黑字幕
    if subs and subs[-1][1] < total_dur_ms - 80:
        s, e, t = subs[-1]
        subs[-1] = (s, total_dur_ms - 1, t)
    return subs


def _fallback_segment_subs(seg: Segment, total_dur_ms: int) -> list[tuple[int, int, str]]:
    """单镜版字幕回退（P3 兼容：转为聚合版调用）。"""
    return _fallback_episode_video_subs([seg], total_dur_ms)


def _resolve_segment_voicelines(db, segment_id) -> tuple[list[str], list[tuple[int, int, str]]]:
    """取该分镜所有成功配音（按 line_index 排序）+ 字幕（按音频时长累加时间轴）。

    P3：替代 _resolve_segment_audio_subs，支持单镜多条 VoiceLine。
    返回 (voice_paths, subs)：
    - voice_paths：按 line_index 排序的本地音频路径列表（含旁白，旁白在最后）
    - subs：[(start_ms, end_ms, text), ...] 按 VoiceLine 时长累加
      字幕文本格式：对白用「speaker：text」，旁白用「旁白：text」
      时间轴：第 i 条 start_ms = sum(前 i 条 duration_ms), end_ms = start_ms + 本条 duration_ms

    无成功配音时返回 ([], [])。
    """
    vls = db.scalars(
        select(VoiceLine).where(
            VoiceLine.segment_id == segment_id,
            VoiceLine.status == MediaStatus.succeeded,
            VoiceLine.audio_url.is_not(None),
        ).order_by(VoiceLine.line_index.asc().nulls_last(), VoiceLine.created_at.asc())
    ).all()

    voice_paths: list[str] = []
    subs: list[tuple[int, int, str]] = []
    # 从 segment 取 speaker 信息（dialogue_lines 按 line_index 对应 VoiceLine）
    seg = db.get(Segment, segment_id)
    dialogue_lines = (seg.dialogue_lines if seg else None) or []
    # 按 line_index 建 speaker 映射
    speaker_by_index: dict[int, str] = {}
    for i, line in enumerate(dialogue_lines):
        if isinstance(line, dict):
            speaker_by_index[i] = (line.get("speaker") or "").strip()

    offset_ms = 0
    for vl in vls:
        local_path = url_to_local_path(vl.audio_url)
        if not local_path or not os.path.exists(local_path):
            continue
        voice_paths.append(local_path)

        # 探测时长：用 vl.duration（由 generate_voice 写入），无则 ffprobe
        dur = vl.duration or 0.0
        if dur <= 0:
            dur = ffprobe_duration(local_path)
        dur_ms = int(dur * 1000)

        # 字幕文本（2026-09-02：去人物名/旁白前缀，只显示正文）
        if vl.is_narration:
            text = _strip_sub_prefix("", vl.text, is_narration=True)
        else:
            speaker = speaker_by_index.get(vl.line_index or -1, "")
            text = _strip_sub_prefix(speaker, vl.text)

        subs.append((offset_ms, offset_ms + dur_ms, text))
        offset_ms += dur_ms

    return voice_paths, subs


def _resolve_segment_audio_subs(db, segment_id):
    """旧版兼容：取该分镜最新成功配音（单条）+ 字幕。

    P3 后推荐用 _resolve_segment_voicelines（支持多对白）。
    """
    voice_path = None
    vl = db.scalar(
        select(VoiceLine).where(
            VoiceLine.segment_id == segment_id,
            VoiceLine.status == MediaStatus.succeeded,
            VoiceLine.audio_url.is_not(None),
        ).order_by(VoiceLine.created_at.desc())
    )
    if vl:
        voice_path = url_to_local_path(vl.audio_url)
    subs = db.scalars(
        select(Subtitle).where(Subtitle.segment_id == segment_id).order_by(Subtitle.start_ms.asc())
    ).all()
    sub_tuples = [(s.start_ms, s.end_ms, s.text) for s in subs]
    return voice_path, sub_tuples


def _collect_bgm_sfx(db, project_id, seg_id_to_ep_id: dict, seg_global_start_ms: dict,
                     seg_global_end_ms: dict) -> tuple[list[dict], list[dict]]:
    """收集项目下所有 done 状态的 BGM/SFX，计算每条的全局起始时间。

    seg_id_to_ep_id: {segment_id_str: episode_id_str}
    seg_global_start_ms: {segment_id_str: 起始毫秒}
    seg_global_end_ms: {segment_id_str: 结束毫秒}

    返回：
      bgm_inputs = [{path, start_ms, volume, duration_ms}]
      sfx_inputs = [{path, start_ms, volume}]
    """
    bgm_inputs: list[dict] = []
    sfx_inputs: list[dict] = []

    # BGM：每条对应一个 episode，起始时间为该 episode 第一个分镜的全局起始
    bgm_tracks = db.scalars(
        select(BgmTrack).where(
            BgmTrack.project_id == project_id,
            BgmTrack.status == "done",
            BgmTrack.audio_url.is_not(None),
        ).order_by(BgmTrack.created_at.asc())
    ).all()

    # 建立 episode_id → 最早 segment 全局起始
    ep_start_ms: dict[str, int] = {}
    ep_end_ms: dict[str, int] = {}
    for sid, eid in seg_id_to_ep_id.items():
        if sid not in seg_global_start_ms:
            continue
        s = seg_global_start_ms[sid]
        e = seg_global_end_ms.get(sid, s)
        eid_str = str(eid)
        if eid_str not in ep_start_ms or s < ep_start_ms[eid_str]:
            ep_start_ms[eid_str] = s
        if eid_str not in ep_end_ms or e > ep_end_ms[eid_str]:
            ep_end_ms[eid_str] = e

    for t in bgm_tracks:
        if not t.episode_id:
            continue
        eid_str = str(t.episode_id)
        if eid_str not in ep_start_ms:
            continue  # episode 无分镜，跳过
        local_path = url_to_local_path(t.audio_url) if t.audio_url else None
        if not local_path:
            continue
        ep_dur_ms = max(0, ep_end_ms[eid_str] - ep_start_ms[eid_str])
        bgm_inputs.append({
            "path": local_path,
            "start_ms": ep_start_ms[eid_str],
            "volume": t.volume,
            "duration_ms": ep_dur_ms,
        })

    # SFX：每条对应一个 segment，起始 = segment 全局起始 + clip.start_time
    sfx_clips = db.scalars(
        select(SfxClip).where(
            SfxClip.status == "done",
            SfxClip.audio_url.is_not(None),
        )
    ).all()
    for c in sfx_clips:
        sid_str = str(c.segment_id)
        if sid_str not in seg_global_start_ms:
            continue
        local_path = url_to_local_path(c.audio_url) if c.audio_url else None
        if not local_path:
            continue
        global_start = seg_global_start_ms[sid_str] + int(c.start_time * 1000)
        sfx_inputs.append({
            "path": local_path,
            "start_ms": global_start,
            "volume": c.volume,
        })

    return bgm_inputs, sfx_inputs


def _compose_export_task(db, task_id: str, project_id, items: list[dict], tmpdir: str,
                         final_path: str, include_voice: bool, include_subtitle: bool,
                         burn_subtitle: bool, include_bgm: bool, include_sfx: bool) -> str:
    """成片拼接主体（export_film 整片 / export_episode 剧集共用）。

    单镜合成（视频 + 配音）→ xfade 拼接 → 混音 BGM/SFX → 字幕硬烧/软字幕。
    返回对外 result_url。tmpdir/final_path 由调用方管理（两种任务的命名不同）。
    """
    seg_cache: dict[str, Segment] = {
        str(s.id): s for s in db.scalars(
            select(Segment).where(Segment.id.in_(
                [it["segment_id"] for it in items if it["kind"] == "video_clip"]
            ))
        ).all()
    }

    update_task(db, task_id, progress=20)

    composed_paths: list[str] = []
    global_subs: list[tuple[int, int, str]] = []  # 累积全局时间轴字幕
    offset_ms = 0          # 未重叠时间轴（逐片段时长纯累加）
    fade_sum_ms = 0        # 已累计转场重叠（xfade 会缩短成片）
    fade_secs: list[float] = []  # 相邻片段间转场时长（len = n-1）
    seg_global_start_ms: dict[str, int] = {}
    seg_global_end_ms: dict[str, int] = {}
    seg_id_to_ep_id: dict[str, str] = {}
    total = len(items)
    # 标准规格：以首个片段视频为准（兜底项目中途改画幅导致各段分辨率/帧率不一致）
    std_w = std_h = None
    try:
        first_vpath = url_to_local_path(items[0]["video_url"])
        if first_vpath:
            std_w, std_h = ffprobe_size(first_vpath)
    except Exception:
        std_w = std_h = None

    for i, item in enumerate(items):
        vpath = url_to_local_path(item["video_url"])
        if not vpath or not os.path.exists(vpath):
            raise ValueError(f"视频文件缺失：{item['video_url']}")

        out_path = os.path.join(tmpdir, f"seg_{i}.mp4")
        actual_start_ms = offset_ms - fade_sum_ms

        if item["kind"] == "video_clip":
            # 逐镜片段（兼容存量）：保留配音拼接 + 单镜字幕
            seg = seg_cache.get(str(item["segment_id"]))
            voice_paths: list[str] = []
            subs: list[tuple[int, int, str]] = []
            if seg:
                voice_paths, subs = _resolve_segment_voicelines(db, seg.id)
                if seg.episode_id:
                    seg_id_to_ep_id[str(seg.id)] = str(seg.episode_id)
            seg_dur = compose_segment_multi(
                vpath, voice_paths, out_path,
                include_voice=include_voice, tmpdir=tmpdir,
                target_w=std_w, target_h=std_h, fps=24,
            )
            composed_paths.append(out_path)

            if include_subtitle and not subs and seg:
                subs = _fallback_segment_subs(seg, int(seg_dur * 1000))
            sid = str(item["segment_id"])
            seg_global_start_ms[sid] = actual_start_ms
            seg_global_end_ms[sid] = actual_start_ms + int(seg_dur * 1000)
            if include_subtitle and subs:
                for (s, e, t) in subs:
                    global_subs.append((actual_start_ms + s, actual_start_ms + e, t))
            offset_ms += int(seg_dur * 1000)
            fade_seg = seg
            fade_ep_id = item["episode_id"]
        else:
            # 幕级视频段（P7 主路径）：自带原生语音 → 不拼接配音，仅统一规格
            segs: list[Segment] = item["segments"]
            # 白模故事版（2026-08-10）：该幕若有成功拼接的动作序列视频 → 替换幕级片段
            from app.models.action_sequence import ActionSequence

            as_row = db.scalar(
                select(ActionSequence).where(
                    ActionSequence.episode_id == item["episode_id"],
                    ActionSequence.status == MediaStatus.succeeded,
                    ActionSequence.composed_url.isnot(None),
                ).order_by(ActionSequence.created_at.desc())
            )
            if as_row is not None:
                replaced = url_to_local_path(as_row.composed_url)
                if replaced and os.path.exists(replaced):
                    vpath = replaced
                    logger.info(
                        "[export] 幕 %s 动作序列替换幕级片段（%s）",
                        item["episode_id"], as_row.composed_url,
                    )
            seg_dur = compose_segment_multi(
                vpath, [], out_path,
                include_voice=False, tmpdir=tmpdir,
                target_w=std_w, target_h=std_h, fps=24,
            )
            composed_paths.append(out_path)

            # 覆盖分镜的全局时间轴（按镜时长比例压缩到段时长内），供 BGM/SFX 定位
            ep_id = item["episode_id"]
            total_dur = sum((s.duration or 5.0) for s in segs) or 1.0
            cursor = 0
            for s in segs:
                sid = str(s.id)
                span = int((s.duration or 5.0) / total_dur * seg_dur * 1000)
                seg_global_start_ms[sid] = actual_start_ms + cursor
                seg_global_end_ms[sid] = actual_start_ms + cursor + span
                seg_id_to_ep_id[sid] = str(ep_id)
                cursor += span
            # 字幕回退：段内台词/旁白按字数分配整段时长
            if include_subtitle:
                subs = _fallback_episode_video_subs(segs, int(seg_dur * 1000))
                for (s, e, t) in subs:
                    global_subs.append((actual_start_ms + s, actual_start_ms + e, t))
            offset_ms += int(seg_dur * 1000)
            fade_seg = segs[-1] if segs else None
            fade_ep_id = ep_id

        # 与下一片段之间的无缝转场：按边界景别关系与情绪自适应（P5 视觉接续）
        # 幕间 0.6s；景别跳级/情绪紧张 0.2s 近硬切；同级叙事 0.3s；舒缓收束 0.6s
        if i < total - 1:
            nxt = items[i + 1]
            next_seg = None
            if nxt["kind"] == "video_clip":
                next_seg = seg_cache.get(str(nxt["segment_id"]))
            elif nxt["segments"]:
                next_seg = nxt["segments"][0]
            is_ep_boundary = bool(
                fade_ep_id and nxt["episode_id"] and fade_ep_id != nxt["episode_id"]
            )
            fade = _transition_fade_secs(fade_seg, next_seg, is_ep_boundary)
            fade_secs.append(fade)
            fade_sum_ms += int(fade * 1000)

        update_task(db, task_id, progress=int(20 + (i + 1) / total * 55))

    # 首尾淡入淡出（apply_fade 时长不变，不影响 xfade 时间轴）
    FADE_SEC = 0.5
    if len(composed_paths) == 1:
        faded = os.path.join(tmpdir, "seg_0_fade.mp4")
        apply_fade(composed_paths[0], faded, fade_in=FADE_SEC, fade_out=FADE_SEC)
        composed_paths[0] = faded
    else:
        head = os.path.join(tmpdir, "seg_0_fade.mp4")
        apply_fade(composed_paths[0], head, fade_in=FADE_SEC)
        composed_paths[0] = head
        tail_idx = len(composed_paths) - 1
        tail = os.path.join(tmpdir, f"seg_{tail_idx}_fade.mp4")
        apply_fade(composed_paths[tail_idx], tail, fade_out=FADE_SEC)
        composed_paths[tail_idx] = tail

    update_task(db, task_id, progress=80)

    # 拼接（2026-09-02 用户要求：片段之间不要转场动画，后续自行剪辑 → 纯 concat 硬切，
    # 不再用 xfade 交叉淡化；总时长 = 各段时长之和）
    concat_path = os.path.join(tmpdir, "concat.mp4")
    concat_clips(composed_paths, concat_path)
    update_task(db, task_id, progress=88)

    # 混音 BGM + SFX
    if include_bgm or include_sfx:
        bgm_inputs, sfx_inputs = _collect_bgm_sfx(
            db, project_id, seg_id_to_ep_id, seg_global_start_ms, seg_global_end_ms,
        )
        # 按开关过滤
        if not include_bgm:
            bgm_inputs = []
        if not include_sfx:
            sfx_inputs = []
        if bgm_inputs or sfx_inputs:
            mixed_path = os.path.join(tmpdir, "mixed.mp4")
            mix_bgm_sfx(concat_path, bgm_inputs, sfx_inputs, mixed_path)
            shutil.copyfile(mixed_path, concat_path)
    update_task(db, task_id, progress=92)

    # 字幕：优先硬烧（Pillow 渲染 PNG + ffmpeg overlay），回退软字幕
    if include_subtitle and global_subs:
        burned = False
        if burn_subtitle:
            try:
                burned = burn_subtitles(concat_path, global_subs, final_path, tmpdir)
            except subprocess.CalledProcessError as e:
                # 硬烧失败（如字幕过多 ffmpeg 滤镜链过长），回退软字幕
                stderr = e.stderr.decode("utf-8", "ignore")[-300:] if e.stderr else ""
                logger.warning("[export] 字幕硬烧失败，回退软字幕: %s", stderr)
        if not burned:
            srt_path = os.path.join(tmpdir, "film.srt")
            write_srt(global_subs, srt_path)
            mux_subtitles(concat_path, srt_path, final_path)
    else:
        shutil.copyfile(concat_path, final_path)

    return f"{settings.static_base_url}/exports/{project_id}/{os.path.basename(final_path)}"


def _extract_cover_frame(video_path: str, cover_path: str) -> None:
    """从成片抽第 1 帧作封面缩略图（best-effort，失败不影响导出）。"""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.1", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", cover_path],
            check=True, capture_output=True, timeout=60,
        )
        logger.info("[export] 封面帧已生成: %s", cover_path)
    except Exception as e:  # noqa: BLE001 - 封面缺失不影响导出
        logger.warning("[export] 封面抽帧失败 %s: %s", video_path, e)


@celery_app.task(name="export_film", bind=True)
def export_film(self, task_id: str, include_voice: bool = True,
                include_subtitle: bool = True, burn_subtitle: bool = True,
                include_bgm: bool = True, include_sfx: bool = True):
    """整片导出（历史任务保留，前端已改用剧集导出）。"""
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="export_")
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        project_id = task.target_id

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)

        from app.services.export_service import collect_export_items
        items = collect_export_items(db, project_id)
        if not items:
            raise ValueError("没有可导出的视频片段")

        out_dir = os.path.join(settings.export_dir, str(project_id))
        os.makedirs(out_dir, exist_ok=True)
        final_path = os.path.join(out_dir, f"film_{task_id}.mp4")
        result_url = _compose_export_task(
            db, task_id, project_id, items, tmpdir, final_path,
            include_voice=include_voice, include_subtitle=include_subtitle,
            burn_subtitle=burn_subtitle, include_bgm=include_bgm, include_sfx=include_sfx,
        )
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=result_url, finished_at=now(),
        )
    except subprocess.CalledProcessError as e:
        db.rollback()
        stderr = e.stderr.decode("utf-8", "ignore")[-800:] if e.stderr else ""
        msg = f"ffmpeg 合成失败：{stderr or e}"
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
    finally:
        db.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


@celery_app.task(name="export_episode", bind=True)
def export_episode(self, task_id: str, include_voice: bool = False,
                   include_subtitle: bool = True, burn_subtitle: bool = True,
                   include_bgm: bool = True, include_sfx: bool = True):
    """剧集导出（2026-08-13）：按幕导出单集成片。

    收集该幕片段（幕级视频优先、逐镜回退）→ 拼接到固定文件名
    exports/{project_id}/ep_{episode_id}.mp4（重新导出覆盖旧成片）→ 抽封面帧。
    """
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="export_ep_")
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        episode_id = task.target_id
        project_id = task.project_id

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)

        from app.services.export_service import collect_export_items
        items = collect_export_items(db, project_id, episode_id=episode_id)
        if not items:
            raise ValueError("该幕没有可导出的视频片段，请先完成幕级视频或分镜的图生视频")

        out_dir = os.path.join(settings.export_dir, str(project_id))
        os.makedirs(out_dir, exist_ok=True)
        final_path = os.path.join(out_dir, f"ep_{episode_id}.mp4")
        result_url = _compose_export_task(
            db, task_id, project_id, items, tmpdir, final_path,
            include_voice=include_voice, include_subtitle=include_subtitle,
            burn_subtitle=burn_subtitle, include_bgm=include_bgm, include_sfx=include_sfx,
        )
        # 封面帧（前端卡片缩略图，约定 ep_{episode_id}_cover.jpg）
        _extract_cover_frame(final_path, os.path.join(out_dir, f"ep_{episode_id}_cover.jpg"))
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=result_url, finished_at=now(),
        )
    except subprocess.CalledProcessError as e:
        db.rollback()
        stderr = e.stderr.decode("utf-8", "ignore")[-800:] if e.stderr else ""
        msg = f"ffmpeg 合成失败：{stderr or e}"
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
    except Exception as e:
        db.rollback()
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
    finally:
        db.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
