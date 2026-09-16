"""OpenAI 兼容 TTS 适配器：POST /audio/speech，覆盖火山/Azure/字节/OpenAI/CosyVoice。

同步返回二进制音频流（非 URL），base64 缓存到 handle.meta，由 task 层落盘。
无 Key 时 test_connection 返回中文提示、synthesize 抛 ProviderError 优雅降级。

差异化配音支持（CosyVoice）：
  - instruct_text 非空 → 请求体加 instruct_text，服务端走 inference_instruct2（情绪控制）
  - emotion 非空（无 instruct_text）→ 自动映射为 instruct_text
  - pitch 非空 → 请求体加 pitch（若服务端支持）
  - 无上述参数 → 回退普通 synthesize，保持对 OpenAI/Azure/火山兼容
"""
import base64
from uuid import uuid4

from app.providers.base import (
    BaseProvider,
    ProviderStatus,
    TaskHandle,
    TaskResult,
    TTSOpts,
)
from app.providers.errors import ProviderError, map_to_chinese


# 情绪标签 → CosyVoice instruct 自然语言指令映射
_EMOTION_INSTRUCT_MAP = {
    "平静": "用平稳自然的语气说",
    "愤怒": "用愤怒且有力的语气说",
    "悲伤": "用悲伤低沉的语气说",
    "欢快": "用欢快轻快的语气说",
    "紧张": "用紧张急促的语气说",
    "温馨": "用温柔温馨的语气说",
    "恐惧": "用恐惧颤抖的语气说",
    "史诗": "用庄重宏大的语气说",
    "冷漠": "用冷漠平淡的语气说",
    "震惊": "用震惊的语气说",
}


def _resolve_instruct_text(opts: TTSOpts) -> str | None:
    """从 opts 解析最终 instruct_text：优先显式 instruct_text，其次 emotion 映射。"""
    if opts.instruct_text:
        return opts.instruct_text
    if opts.emotion:
        return _EMOTION_INSTRUCT_MAP.get(opts.emotion, f"用{opts.emotion}的语气说")
    return None


class OpenAITTSProvider(BaseProvider):
    provider_type = "openai_tts"

    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> TaskHandle:
        url = f"{self.cfg.endpoint}/audio/speech"
        body = {
            "model": self.cfg.model_id,
            "input": text,
            "voice": voice_id or opts.voice,
            "response_format": opts.response_format,
            "speed": opts.speed,
        }
        # 差异化配音：CosyVoice instruct 模式
        instruct_text = _resolve_instruct_text(opts)
        if instruct_text:
            body["instruct_text"] = instruct_text
        if opts.pitch is not None:
            body["pitch"] = opts.pitch
        # CosyVoice zero_shot 一次性参考音频（优先于 voice 字段）
        if opts.prompt_wav:
            body["prompt_wav"] = opts.prompt_wav
            if opts.prompt_text:
                body["prompt_text"] = opts.prompt_text
        # 有 key 带 Authorization，无 key（本地 CosyVoice 等）不带
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = self.http.post(url, headers=headers, json=body, timeout=120)
        r.raise_for_status()  # 失败抛 HTTPStatusError → task 层 map_to_chinese
        # /audio/speech 直接返回二进制音频流，不是 URL
        audio_b64 = base64.b64encode(r.content).decode()
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=f"sync-{uuid4().hex}",
            meta={
                "audio_bytes_b64": audio_b64,
                "format": opts.response_format,
                "instruct_text": instruct_text,  # 记录实际使用的指令，便于调试
            },
        )

    def getTaskResult(self, handle: TaskHandle) -> TaskResult:
        # 同步适配器，meta 已缓存结果；audioUrl 由 task 层落盘后回填
        if "audio_bytes_b64" in handle.meta:
            return TaskResult(
                status=ProviderStatus.succeeded,
                audioUrl=None,  # task 层 bytes_to_local 后回填 VoiceLine.audio_url
                raw={"format": handle.meta.get("format", "mp3")},
            )
        return TaskResult(status=ProviderStatus.failed, error="无音频数据")

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.synthesize("测试", "default", TTSOpts(response_format="wav"))
            return True, "OK"
        except Exception as e:
            return False, map_to_chinese(e)
