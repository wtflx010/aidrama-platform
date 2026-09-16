"""OpenAITTSProvider 契约测试（含差异化配音 instruct/emotion）。

覆盖：
1. 普通 synthesize（无 instruct/emotion）→ 请求体不含 instruct_text
2. instruct_text 显式传入 → 请求体含 instruct_text
3. emotion 映射 → 请求体 instruct_text 为映射后的中文指令
4. pitch 传入 → 请求体含 pitch
5. 无 api_key 时不带 Authorization（本地 CosyVoice）
"""
import base64

import respx
import httpx

from app.providers.openai_tts import OpenAITTSProvider, _EMOTION_INSTRUCT_MAP, _resolve_instruct_text
from app.providers.base import TTSOpts


COSYVOICE = "http://localhost:9880"


def _fake_audio_bytes() -> bytes:
    """构造一段假的 wav bytes（非真实音频，仅用于断言落盘）。"""
    return b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


@respx.mock
def test_synthesize_plain_no_instruct(tts_model):
    """普通合成（无 instruct/emotion）→ 请求体不含 instruct_text/pitch。"""
    route = respx.post(f"{COSYVOICE}/audio/speech").respond(content=_fake_audio_bytes())
    provider = OpenAITTSProvider(model_config=tts_model)
    opts = TTSOpts(voice="default", response_format="wav")

    handle = provider.synthesize("测试对白", "default", opts)

    assert route.called
    body = route.calls[0].request.read().decode()
    assert "instruct_text" not in body
    assert "pitch" not in body
    assert handle.meta["audio_bytes_b64"] == base64.b64encode(_fake_audio_bytes()).decode()
    assert handle.meta["instruct_text"] is None


@respx.mock
def test_synthesize_with_explicit_instruct_text(tts_model):
    """显式 instruct_text → 请求体含该指令，meta 记录之。"""
    route = respx.post(f"{COSYVOICE}/audio/speech").respond(content=_fake_audio_bytes())
    provider = OpenAITTSProvider(model_config=tts_model)
    opts = TTSOpts(voice="林浅", instruct_text="用愤怒且急促的语气说")

    provider.synthesize("你怎么能这样！", "林浅", opts)

    body = route.calls[0].request.read().decode()
    assert "用愤怒且急促的语气说" in body
    assert "instruct_text" in body


@respx.mock
def test_synthesize_emotion_mapped_to_instruct(tts_model):
    """emotion 标签 → 自动映射为 instruct_text（无显式 instruct_text 时）。"""
    route = respx.post(f"{COSYVOICE}/audio/speech").respond(content=_fake_audio_bytes())
    provider = OpenAITTSProvider(model_config=tts_model)
    opts = TTSOpts(voice="林浅", emotion="悲伤")

    provider.synthesize("我再也见不到你了。", "林浅", opts)

    body = route.calls[0].request.read().decode()
    expected = _EMOTION_INSTRUCT_MAP["悲伤"]
    assert expected in body
    assert "instruct_text" in body


@respx.mock
def test_synthesize_with_pitch(tts_model):
    """pitch 非空 → 请求体含 pitch。"""
    route = respx.post(f"{COSYVOICE}/audio/speech").respond(content=_fake_audio_bytes())
    provider = OpenAITTSProvider(model_config=tts_model)
    opts = TTSOpts(voice="default", pitch=1.2)

    provider.synthesize("测试", "default", opts)

    body = route.calls[0].request.read().decode()
    assert "pitch" in body


@respx.mock
def test_synthesize_no_api_key_no_auth_header(tts_model):
    """本地 CosyVoice（api_key_ref 为空）→ 请求不带 Authorization。"""
    route = respx.post(f"{COSYVOICE}/audio/speech").respond(content=_fake_audio_bytes())
    provider = OpenAITTSProvider(model_config=tts_model)

    provider.synthesize("测试", "default", TTSOpts())

    req = route.calls[0].request
    assert "authorization" not in {k.lower() for k in req.headers.keys()}


def test_resolve_instruct_text_priority():
    """instruct_text 优先于 emotion；两者皆空返回 None。"""
    # 显式 instruct_text 优先
    opts1 = TTSOpts(instruct_text="自定义指令", emotion="愤怒")
    assert _resolve_instruct_text(opts1) == "自定义指令"
    # emotion 映射
    opts2 = TTSOpts(emotion="愤怒")
    assert _resolve_instruct_text(opts2) == _EMOTION_INSTRUCT_MAP["愤怒"]
    # 未知 emotion 兜底
    opts3 = TTSOpts(emotion="害羞")
    assert _resolve_instruct_text(opts3) == "用害羞的语气说"
    # 都空
    opts4 = TTSOpts()
    assert _resolve_instruct_text(opts4) is None


@respx.mock
def test_synthesize_instruct_text_recorded_in_meta(tts_model):
    """handle.meta 记录实际使用的 instruct_text，便于调试。"""
    respx.post(f"{COSYVOICE}/audio/speech").respond(content=_fake_audio_bytes())
    provider = OpenAITTSProvider(model_config=tts_model)
    opts = TTSOpts(emotion="欢快")

    handle = provider.synthesize("太好了！", "default", opts)

    assert handle.meta["instruct_text"] == _EMOTION_INSTRUCT_MAP["欢快"]
