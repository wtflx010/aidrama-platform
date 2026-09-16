"""OpenAICompatibleProvider 契约测试。

覆盖三类核心场景：
1. textToImage：纯文生图，断言请求体不含 extra_body（踩坑#1）
2. imageToImage + Base64：本地 media URL → data URI 转换（踩坑#6）
3. imageToImage + 公网 URL：原样透传
4. test_connection：模型连接测试
5. chat：文本对话
"""
import respx
import httpx

from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.base import ImageOpts, ProviderStatus

AGNES = "https://apihub.agnes-ai.cn/v1"


# ─── textToImage：纯文生图 ─────────────────────────────────────────

@respx.mock
def test_text_to_image_returns_urls(image_model_text2img, load_fixture):
    """文生图：mock /images/generations 返回 → 断言 imageUrls 正确解析。"""
    resp = load_fixture("text_to_image")
    respx.post(f"{AGNES}/images/generations").respond(json=resp)

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    handle = provider.textToImage("雨夜咖啡馆", ImageOpts(n=1, size="2K", ratio="16:9"))
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.succeeded
    assert len(result.imageUrls) == 1
    assert result.imageUrls[0].startswith("https://platform-outputs.agnes-ai.space/images/")


@respx.mock
def test_text_to_image_no_extra_body(image_model_text2img, load_fixture):
    """踩坑#1：纯文生图绝不传 extra_body / response_format。"""
    resp = load_fixture("text_to_image")
    route = respx.post(f"{AGNES}/images/generations").respond(json=resp)

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    provider.textToImage("test prompt", ImageOpts(n=1))

    sent_body = route.calls.last.request.read()
    import json
    body = json.loads(sent_body)
    assert "extra_body" not in body
    assert "response_format" not in body


@respx.mock
def test_text_to_image_sends_size_and_ratio(image_model_text2img, load_fixture):
    """文生图请求体应包含 size 和 ratio。"""
    resp = load_fixture("text_to_image")
    route = respx.post(f"{AGNES}/images/generations").respond(json=resp)

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    provider.textToImage("test", ImageOpts(n=1, size="2K", ratio="16:9"))

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["size"] == "2K"
    assert body["ratio"] == "16:9"
    assert body["model"] == "agnes-image-2.1-flash"


# ─── imageToImage：图生图 + Base64 转换 ────────────────────────────

@respx.mock
def test_image_to_image_local_url_converted_to_data_uri(image_model_img2img, load_fixture, tmp_path, monkeypatch):
    """踩坑#6：本地 media URL 须转 base64 data URI，Agnes 拒绝 localhost URL。"""
    # 准备一张本地测试图片
    from app.config import settings
    media_dir = tmp_path / "media"
    asset_dir = media_dir / "assets" / "test-asset"
    asset_dir.mkdir(parents=True)
    img_path = asset_dir / "cover.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)  # 最小 PNG 头

    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    local_url = "http://localhost:8000/static/media/assets/test-asset/cover.png"
    resp = load_fixture("image_to_image")
    route = respx.post(f"{AGNES}/images/generations").respond(json=resp)

    provider = OpenAICompatibleProvider(model_config=image_model_img2img)
    provider.imageToImage("character front view", [local_url], ImageOpts(n=1, size="2K"))

    import json
    body = json.loads(route.calls.last.request.read())
    # 请求体中 image 字段应为 data URI，不是 localhost URL
    sent_image_url = body["extra_body"]["image"][0]
    assert sent_image_url.startswith("data:image/png;base64,")
    assert "localhost" not in sent_image_url
    assert "static/media" not in sent_image_url


@respx.mock
def test_image_to_image_public_url_passthrough(image_model_img2img, load_fixture):
    """公网 URL 应原样透传，不做 Base64 转换。"""
    resp = load_fixture("image_to_image")
    route = respx.post(f"{AGNES}/images/generations").respond(json=resp)

    public_url = "https://platform-outputs.agnes-ai.space/images/t2i/some_cover.png"
    provider = OpenAICompatibleProvider(model_config=image_model_img2img)
    provider.imageToImage("front view", [public_url], ImageOpts(n=1))

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["extra_body"]["image"][0] == public_url


@respx.mock
def test_image_to_image_extra_body_format(image_model_img2img, load_fixture):
    """踩坑#1：图生图/多图合成必须传 extra_body={image:[urls], response_format:"url"}（无需 tags）。"""
    resp = load_fixture("image_to_image")
    route = respx.post(f"{AGNES}/images/generations").respond(json=resp)

    public_url = "https://example.com/cover.png"
    provider = OpenAICompatibleProvider(model_config=image_model_img2img)
    provider.imageToImage("test", [public_url], ImageOpts(n=1))

    import json
    body = json.loads(route.calls.last.request.read())
    assert "tags" not in body["extra_body"]
    assert body["extra_body"]["image"] == [public_url]
    assert body["extra_body"]["response_format"] == "url"
    assert body["model"] == "agnes-image-2.0-flash"


@respx.mock
def test_image_to_image_multi_image(image_model_img2img, load_fixture):
    """多图合成：多个参考图（场景/角色/道具）应全部传入 image 数组。"""
    resp = load_fixture("image_to_image")
    route = respx.post(f"{AGNES}/images/generations").respond(json=resp)

    urls = [
        "https://example.com/scene.png",
        "https://example.com/char1.png",
        "https://example.com/char2.png",
        "https://example.com/prop.png",
    ]
    provider = OpenAICompatibleProvider(model_config=image_model_img2img)
    provider.imageToImage("test", urls, ImageOpts(n=1))

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["extra_body"]["image"] == urls


@respx.mock
def test_image_to_image_returns_urls(image_model_img2img, load_fixture):
    """图生图返回结果应正确解析 imageUrls。"""
    resp = load_fixture("image_to_image")
    respx.post(f"{AGNES}/images/generations").respond(json=resp)

    provider = OpenAICompatibleProvider(model_config=image_model_img2img)
    handle = provider.imageToImage("test", ["https://example.com/cover.png"], ImageOpts(n=1))
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.succeeded
    assert len(result.imageUrls) == 1


# ─── chat：文本对话 ────────────────────────────────────────────────

@respx.mock
def test_chat(text_model):
    """文本模型 chat 调用。"""
    respx.post(f"{AGNES}/chat/completions").respond(json={
        "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        "model": "agnes-2.0-flash",
    })

    provider = OpenAICompatibleProvider(model_config=text_model)
    result = provider.chat([{"role": "user", "content": "ping"}])

    assert result["choices"][0]["message"]["content"] == "pong"


# ─── test_connection ───────────────────────────────────────────────

@respx.mock
def test_connection_image_model(image_model_text2img, load_fixture):
    """image 模型 test_connection：成功生成图返回 OK。"""
    resp = load_fixture("text_to_image")
    respx.post(f"{AGNES}/images/generations").respond(json=resp)

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    ok, msg = provider.test_connection()

    assert ok is True
    assert "OK" in msg


# ─── Agnes 图片队列满（503 image queue is full）───────────────────

def _queue_full_resp() -> httpx.Response:
    return httpx.Response(
        503, json={"error": {"message": "image queue is full, please retry later"}}
    )


@respx.mock
def test_queue_full_retries_then_success(image_model_text2img, load_fixture, monkeypatch):
    """503 image queue is full → 自动退避重试，队列恢复后成功（不直接失败）。"""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda s: None)
    resp = load_fixture("text_to_image")
    route = respx.post(f"{AGNES}/images/generations")
    route.side_effect = [
        _queue_full_resp(),
        _queue_full_resp(),
        httpx.Response(200, json=resp),
    ]

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    handle = provider.textToImage("雨夜咖啡馆", ImageOpts(n=1))
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.succeeded
    assert len(result.imageUrls) == 1
    assert route.call_count == 3  # 2 次队列满 + 1 次成功


@respx.mock
def test_queue_full_exhausted_raises_clear_error(image_model_text2img, monkeypatch):
    """队列持续打满（重试耗尽）→ 抛中文 ProviderError，而非晦涩的 503。"""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda s: None)
    route = respx.post(f"{AGNES}/images/generations")
    route.side_effect = [_queue_full_resp()] * 5

    import pytest
    from app.providers.errors import ProviderError

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    with pytest.raises(ProviderError, match="图片生成队列已满"):
        provider.textToImage("test", ImageOpts(n=1))
    assert route.call_count == 5


@respx.mock
def test_generic_503_not_queue_full_not_retried_forever(image_model_text2img, monkeypatch):
    """非队列满的 503（如网关错误）走通用 5xx 重试（2 次），不误判为队列满。"""
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda s: None)
    route = respx.post(f"{AGNES}/images/generations")
    # 通用 503（无 queue is full 字样）
    route.side_effect = [
        httpx.Response(503, json={"error": {"message": "something else"}}),
        httpx.Response(503, json={"error": {"message": "something else"}}),
        httpx.Response(503, json={"error": {"message": "something else"}}),
    ]

    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        provider = OpenAICompatibleProvider(model_config=image_model_text2img)
        provider.textToImage("test", ImageOpts(n=1))
    # 通用重试 2 次 + 最终失败 = 3 次请求
    assert route.call_count == 3


@respx.mock
def test_connection_text_model(text_model):
    """text 模型 test_connection：成功 chat 返回 OK。"""
    respx.post(f"{AGNES}/chat/completions").respond(json={
        "choices": [{"message": {"content": "pong"}}],
    })

    provider = OpenAICompatibleProvider(model_config=text_model)
    ok, msg = provider.test_connection()

    assert ok is True
    assert msg == "OK"


@respx.mock
def test_connection_failure(image_model_text2img):
    """image 模型 test_connection：API 报错时返回 False。"""
    respx.post(f"{AGNES}/images/generations").respond(status_code=401)

    provider = OpenAICompatibleProvider(model_config=image_model_text2img)
    ok, msg = provider.test_connection()

    assert ok is False
    assert msg  # 有错误信息
