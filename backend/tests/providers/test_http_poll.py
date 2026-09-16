"""HttpPollProvider 契约测试。

覆盖三类核心场景：
1. imageToVideo：提交视频任务，断言 estimatedSeconds 是 int（踩坑#7）
2. imageToVideo + Base64：本地首帧 URL → data URI（踩坑#6）
3. getTaskResult：从 metadata.url 取视频地址（踩坑#2/3 result_jsonpath）
4. getTaskResult：失败/运行中状态
5. test_connection
"""
import respx

from app.providers.http_poll import HttpPollProvider
from app.providers.base import VideoOpts, ProviderStatus

AGNES = "https://apihub.agnes-ai.cn/v1"
VIDEO_TASK_ID = "task_X1mRKvdVEy2YE27EgG2uKibSahnegS20"


# ─── imageToVideo：提交 ────────────────────────────────────────────

@respx.mock
def test_image_to_video_submit(video_model, load_fixture):
    """提交视频任务：mock POST /videos 返回 task_id。"""
    resp = load_fixture("video_submit")
    respx.post(f"{AGNES}/videos").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    opts = VideoOpts(prompt="雨夜街道", width=1280, height=720, num_frames=41, frame_rate=24)
    handle = provider.imageToVideo("https://example.com/frame.png", None, opts)

    assert handle.providerTaskId == VIDEO_TASK_ID
    assert handle.pollUrl == f"{AGNES}/videos/{VIDEO_TASK_ID}"


@respx.mock
def test_image_to_video_estimated_seconds_is_int(video_model, load_fixture):
    """踩坑#7：estimatedSeconds 必须是 int，不能是 float。

    num_frames=41, frame_rate=24 → 41/24=1.708(float)，须 int() 转换。
    """
    resp = load_fixture("video_submit")
    respx.post(f"{AGNES}/videos").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    opts = VideoOpts(prompt="test", num_frames=41, frame_rate=24)
    handle = provider.imageToVideo("https://example.com/frame.png", None, opts)

    assert isinstance(handle.estimatedSeconds, int)
    assert handle.estimatedSeconds == 1  # int(41/24) == 1


@respx.mock
def test_image_to_video_estimated_seconds_uses_duration(video_model, load_fixture):
    """有 duration 时优先用 duration。"""
    resp = load_fixture("video_submit")
    respx.post(f"{AGNES}/videos").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    opts = VideoOpts(prompt="test", num_frames=121, frame_rate=24, duration=5.0)
    handle = provider.imageToVideo("https://example.com/frame.png", None, opts)

    assert handle.estimatedSeconds == 5


@respx.mock
def test_image_to_video_local_url_converted_to_data_uri(video_model, load_fixture, tmp_path, monkeypatch):
    """踩坑#6：本地首帧 URL 须转 base64 data URI。"""
    from app.config import settings
    media_dir = tmp_path / "media"
    kf_dir = media_dir / "keyframes" / "test-kf"
    kf_dir.mkdir(parents=True)
    img_path = kf_dir / "frame.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    monkeypatch.setattr(settings, "media_dir", str(media_dir))

    local_url = "http://localhost:8000/static/media/keyframes/test-kf/frame.png"
    resp = load_fixture("video_submit")
    route = respx.post(f"{AGNES}/videos").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    opts = VideoOpts(prompt="test", num_frames=41, frame_rate=24)
    provider.imageToVideo(local_url, None, opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["image"].startswith("data:image/png;base64,")
    assert "localhost" not in body["image"]


@respx.mock
def test_image_to_video_with_last_frame(video_model, load_fixture):
    """有尾帧时 image_tail 字段应传入。"""
    resp = load_fixture("video_submit")
    route = respx.post(f"{AGNES}/videos").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    opts = VideoOpts(prompt="test", num_frames=41, frame_rate=24)
    provider.imageToVideo("https://example.com/first.png", "https://example.com/last.png", opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["image"] == "https://example.com/first.png"
    assert body["image_tail"] == "https://example.com/last.png"


@respx.mock
def test_image_to_video_request_body_fields(video_model, load_fixture):
    """提交请求体应包含所有必要字段。"""
    resp = load_fixture("video_submit")
    route = respx.post(f"{AGNES}/videos").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    opts = VideoOpts(prompt="雨夜街道", width=1280, height=720, num_frames=41, frame_rate=24)
    provider.imageToVideo("https://example.com/frame.png", None, opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["model"] == "agnes-video-v2.0"
    assert body["prompt"] == "雨夜街道"
    assert body["width"] == 1280
    assert body["height"] == 720
    assert body["num_frames"] == 41
    assert body["frame_rate"] == 24


# ─── getTaskResult：轮询结果 ───────────────────────────────────────

@respx.mock
def test_get_task_result_completed(video_model, load_fixture):
    """踩坑#2/3：completed 状态从 metadata.url 取视频地址。"""
    resp = load_fixture("video_query_completed")
    respx.get(f"{AGNES}/videos/{VIDEO_TASK_ID}").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    from app.providers.base import TaskHandle
    handle = TaskHandle(
        provider="http_poll",
        providerTaskId=VIDEO_TASK_ID,
        pollUrl=f"{AGNES}/videos/{VIDEO_TASK_ID}",
    )
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.succeeded
    assert result.videoUrl == "https://platform-outputs.agnes-ai.space/videos/agnes-video-v2.0/task_X1mRKvdVEy2YE27EgG2uKibSahnegS20.mp4"


@respx.mock
def test_get_task_result_completed_but_url_missing(video_model):
    """竞态修复：status=completed 但 metadata.url 尚未就绪 → 返回 running 继续轮询。"""
    resp = {
        "task_id": VIDEO_TASK_ID,
        "status": "completed",
        "metadata": {},  # url 尚未就绪
    }
    respx.get(f"{AGNES}/videos/{VIDEO_TASK_ID}").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    from app.providers.base import TaskHandle
    handle = TaskHandle(
        provider="http_poll",
        providerTaskId=VIDEO_TASK_ID,
        pollUrl=f"{AGNES}/videos/{VIDEO_TASK_ID}",
    )
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.running
    assert result.videoUrl is None


@respx.mock
def test_get_task_result_in_progress(video_model, load_fixture):
    """in_progress 状态应返回 running。"""
    resp = load_fixture("video_query_pending")
    respx.get(f"{AGNES}/videos/{VIDEO_TASK_ID}").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    from app.providers.base import TaskHandle
    handle = TaskHandle(
        provider="http_poll",
        providerTaskId=VIDEO_TASK_ID,
        pollUrl=f"{AGNES}/videos/{VIDEO_TASK_ID}",
    )
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.running
    assert result.videoUrl is None


@respx.mock
def test_get_task_result_failed(video_model, load_fixture):
    """failed 状态应返回 failed + error 信息。"""
    resp = load_fixture("video_query_failed")
    respx.get(f"{AGNES}/videos/{VIDEO_TASK_ID}").respond(json=resp)

    provider = HttpPollProvider(model_config=video_model)
    from app.providers.base import TaskHandle
    handle = TaskHandle(
        provider="http_poll",
        providerTaskId=VIDEO_TASK_ID,
        pollUrl=f"{AGNES}/videos/{VIDEO_TASK_ID}",
    )
    result = provider.getTaskResult(handle)

    assert result.status == ProviderStatus.failed
    assert result.error  # 有错误信息
    assert result.videoUrl is None


# ─── test_connection ───────────────────────────────────────────────

@respx.mock
def test_connection_ok(video_model):
    """test_connection：GET submit_path 返回非 401/403 即可达。"""
    respx.get(f"{AGNES}/videos").respond(status_code=404)

    provider = HttpPollProvider(model_config=video_model)
    ok, msg = provider.test_connection()

    assert ok is True
    assert "OK" in msg


@respx.mock
def test_connection_auth_failure(video_model):
    """test_connection：401 表示密钥无效。"""
    respx.get(f"{AGNES}/videos").respond(status_code=401)

    provider = HttpPollProvider(model_config=video_model)
    ok, msg = provider.test_connection()

    assert ok is False
    assert "密钥" in msg


# ─── body_template 通用化 ──────────────────────────────────────────

def _make_template_model(body_template):
    """构造带 body_template 的模型（模拟可灵/Runway 等第三方 API）。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        endpoint="https://api.example.com/v1",
        model_id="kling-video-v1",
        model_type="video",
        provider_type="http_poll",
        api_key_ref="EXAMPLE_KEY",
        http_poll_config={
            "submit_path": "/videos",
            "query_path": "/videos/{task_id}",
            "task_id_jsonpath": "$.data.task_id",
            "status_jsonpath": "$.data.status",
            "result_jsonpath": "$.data.video_url",
            "body_template": body_template,
        },
    )


@respx.mock
def test_body_template_renders_variables():
    """body_template：{variable} 占位符正确替换。"""
    model = _make_template_model({
        "model": "{model_id}",
        "prompt": "{prompt}",
        "image": "{firstFrame}",
        "duration": "{duration}",
    })
    route = respx.post("https://api.example.com/v1/videos").respond(
        json={"data": {"task_id": "ext_task_123"}}
    )

    provider = HttpPollProvider(model_config=model)
    opts = VideoOpts(prompt="猫在跑", num_frames=121, frame_rate=24)
    provider.imageToVideo("https://cdn.example.com/frame.jpg", None, opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["model"] == "kling-video-v1"
    assert body["prompt"] == "猫在跑"
    assert body["image"] == "https://cdn.example.com/frame.jpg"  # 公网 URL 不转 base64
    assert abs(body["duration"] - 5.04) < 0.01  # 121/24 ≈ 5.04 → float


@respx.mock
def test_body_template_static_values():
    """body_template：无占位符的值作为静态字段保留。"""
    model = _make_template_model({
        "model": "{model_id}",
        "mode": "std",
        "quality": "high",
    })
    route = respx.post("https://api.example.com/v1/videos").respond(
        json={"data": {"task_id": "t1"}}
    )

    provider = HttpPollProvider(model_config=model)
    opts = VideoOpts(prompt="test", num_frames=41, frame_rate=24)
    provider.imageToVideo("https://example.com/f.png", None, opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["mode"] == "std"
    assert body["quality"] == "high"


@respx.mock
def test_body_template_coerce_int():
    """body_template：数字字符串自动转 int。"""
    model = _make_template_model({
        "width": "{width}",
        "height": "{height}",
        "num_frames": "{num_frames}",
    })
    route = respx.post("https://api.example.com/v1/videos").respond(
        json={"data": {"task_id": "t1"}}
    )

    provider = HttpPollProvider(model_config=model)
    opts = VideoOpts(prompt="test", width=1280, height=720, num_frames=121, frame_rate=24)
    provider.imageToVideo("https://example.com/f.png", None, opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["width"] == 1280
    assert isinstance(body["width"], int)
    assert body["num_frames"] == 121
    assert isinstance(body["num_frames"], int)


@respx.mock
def test_body_template_non_string_values():
    """body_template：非字符串值（int/bool/list）直接使用。"""
    model = _make_template_model({
        "model": "{model_id}",
        "count": 1,
        "enabled": True,
        "tags": ["video", "hd"],
    })
    route = respx.post("https://api.example.com/v1/videos").respond(
        json={"data": {"task_id": "t1"}}
    )

    provider = HttpPollProvider(model_config=model)
    opts = VideoOpts(prompt="test", num_frames=41, frame_rate=24)
    provider.imageToVideo("https://example.com/f.png", None, opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["count"] == 1
    assert body["enabled"] is True
    assert body["tags"] == ["video", "hd"]


@respx.mock
def test_body_template_last_frame():
    """body_template：lastFrame 变量正确渲染。"""
    model = _make_template_model({
        "image": "{firstFrame}",
        "image_tail": "{lastFrame}",
    })
    route = respx.post("https://api.example.com/v1/videos").respond(
        json={"data": {"task_id": "t1"}}
    )

    provider = HttpPollProvider(model_config=model)
    opts = VideoOpts(prompt="test", num_frames=41, frame_rate=24)
    provider.imageToVideo("https://example.com/first.png", "https://example.com/last.png", opts)

    import json
    body = json.loads(route.calls.last.request.read())
    assert body["image"] == "https://example.com/first.png"
    assert body["image_tail"] == "https://example.com/last.png"


def test_coerce_int():
    """_coerce：数字字符串转 int。"""
    assert HttpPollProvider._coerce("1280") == 1280
    assert isinstance(HttpPollProvider._coerce("1280"), int)


def test_coerce_float():
    """_coerce：浮点字符串转 float。"""
    assert HttpPollProvider._coerce("1.5") == 1.5
    assert isinstance(HttpPollProvider._coerce("1.5"), float)


def test_coerce_bool():
    """_coerce：true/false 转 bool。"""
    assert HttpPollProvider._coerce("true") is True
    assert HttpPollProvider._coerce("false") is False


def test_coerce_string():
    """_coerce：非数字字符串保持原样。"""
    assert HttpPollProvider._coerce("hello") == "hello"
    assert HttpPollProvider._coerce("kling-v1") == "kling-v1"
