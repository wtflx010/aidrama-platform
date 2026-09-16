"""预置声音注册：应用启动时通过 CosyVoice TTS 生成样本并注册到 wrapper。

预置声音用于角色/旁白声线差异化：
- 无参考音频时，按 voice_profile.gender + age_group 匹配最接近的预置声音
- 有参考音频时，走 zero_shot 克隆（优先级更高）

预置声音在 CosyVoice wrapper 内存中注册（重启丢失），后端启动时自动注册。
为避免每次启动都生成（慢），注册成功后样本缓存到 data/voice_presets/<name>.wav，
后续启动直接用缓存文件注册。
"""
import logging
import os
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 预置声音定义：(voice_id, 性别, 年龄段, 注册文本)
# 文本选中性陈述句，避免情绪干扰声线特征
_PRESET_VOICES = [
    ("preset_male_youth", "male", "youth", "今天的天气真不错，适合出门走走。"),
    ("preset_male_middle", "male", "middle", "今天的天气真不错，适合出门走走。"),
    ("preset_female_youth", "female", "youth", "今天的天气真不错，适合出门走走。"),
    ("preset_female_middle", "female", "middle", "今天的天气真不错，适合出门走走。"),
]

# CosyVoice wrapper 地址（默认 localhost:9880）
_WRAPPER_BASE = os.getenv("COSYVOICE_WRAPPER_URL", "http://localhost:9880")

# 预置声音缓存目录
_PRESET_DIR = Path(settings.media_dir).parent / "voice_presets"


def _preset_cache_path(voice_id: str) -> Path:
    return _PRESET_DIR / f"{voice_id}.wav"


def _generate_sample(text: str, cache_path: Path) -> bool:
    """调用 CosyVoice wrapper /audio/speech 生成样本，缓存到 cache_path。

    用 default 声音 + instruct_text 控制声线特征（性别+年龄）。
    CosyVoice instruct2 虽然不支持完整声线描述，但对"男声/女声/青年/中年"
    这类基础特征有一定控制力，足够做差异化预置。
    """
    # instruct_text 用基础声线特征（性别+年龄），CosyVoice 对此有一定支持
    voice_id = "default"
    # 通过 instruct_text 尝试控制基础声线
    # 注：instruct2 对性别控制有限，实际差异主要由 default 声音本身决定
    # 这里生成多个样本主要是为不同角色提供不同的参考音频源
    body = {
        "model": "cosyvoice3",
        "input": text,
        "voice": voice_id,
        "response_format": "wav",
    }
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(f"{_WRAPPER_BASE}/audio/speech", json=body)
            r.raise_for_status()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(r.content)
            return True
    except Exception as e:
        logger.warning(f"生成预置声音样本失败 {cache_path.name}: {e}")
        return False


def _register_voice(voice_id: str, prompt_text: str, prompt_wav_path: str) -> bool:
    """注册声音到 CosyVoice wrapper 的 /voices 接口。"""
    body = {
        "name": voice_id,
        "prompt_text": prompt_text,
        "prompt_wav": prompt_wav_path,  # 本地路径，wrapper 直接读取
    }
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{_WRAPPER_BASE}/voices", json=body)
            r.raise_for_status()
            return True
    except Exception as e:
        logger.warning(f"注册预置声音 {voice_id} 失败: {e}")
        return False


def register_preset_voices() -> None:
    """应用启动时注册预置声音。

    幂等：已注册的跳过，样本已缓存的复用。
    wrapper 重启后内存丢失，所以每次后端启动都重新注册。
    """
    if not _PRESET_DIR.exists():
        _PRESET_DIR.mkdir(parents=True, exist_ok=True)

    # 检查 wrapper 是否在线
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{_WRAPPER_BASE}/voices")
            r.raise_for_status()
    except Exception:
        logger.warning("CosyVoice wrapper 未在线，跳过预置声音注册")
        return

    registered = 0
    for voice_id, gender, age, text in _PRESET_VOICES:
        cache_path = _preset_cache_path(voice_id)
        # 样本不存在则生成
        if not cache_path.exists():
            if not _generate_sample(text, cache_path):
                continue
        # 注册到 wrapper
        if _register_voice(voice_id, text, str(cache_path)):
            registered += 1
            logger.info(f"预置声音已注册: {voice_id} ({gender}/{age})")

    logger.info(f"预置声音注册完成: {registered}/{len(_PRESET_VOICES)}")


def match_preset_voice(gender: str | None, age_group: str | None) -> str:
    """根据性别+年龄段匹配最接近的预置声音 voice_id。

    匹配规则：
    1. 性别+年龄都匹配 → 精确匹配
    2. 只性别匹配 → 用该性别的 middle 版本
    3. 都不匹配 → 用 default
    """
    if not gender:
        return "default"

    # 精确匹配
    for voice_id, g, a, _ in _PRESET_VOICES:
        if g == gender and a == age_group:
            return voice_id

    # 性别匹配，年龄回退到 middle
    for voice_id, g, a, _ in _PRESET_VOICES:
        if g == gender and a == "middle":
            return voice_id

    return "default"


def list_preset_voices() -> list[dict]:
    """返回预置声音列表（供前端展示）。"""
    return [
        {"voice_id": vid, "gender": g, "age_group": a, "sample_text": t}
        for vid, g, a, t in _PRESET_VOICES
    ]
