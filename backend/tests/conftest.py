"""Pytest 全局 fixture：mock Model 配置 + Agnes API 响应快照加载。

测试不依赖数据库/真实网络：用 SimpleNamespace 构造 Model，respx 拦截 httpx 请求。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

# Agnes 测试端点（respx mock 用，不发起真实请求）
AGNES_ENDPOINT = "https://apihub.agnes-ai.cn/v1"
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "agnes_responses"


# ─── Mock Model 配置 ───────────────────────────────────────────────

@pytest.fixture
def image_model_text2img():
    """文生图模型 agnes-image-2.1-flash（纯文生图，禁 extra_body）。"""
    return SimpleNamespace(
        endpoint=AGNES_ENDPOINT,
        model_id="agnes-image-2.1-flash",
        model_type="image",
        provider_type="openai_compatible",
        api_key_ref="AGNES_API_KEY",
        http_poll_config=None,
    )


@pytest.fixture
def image_model_img2img():
    """图生图模型 agnes-image-2.0-flash（需 extra_body tags=img2img）。"""
    return SimpleNamespace(
        endpoint=AGNES_ENDPOINT,
        model_id="agnes-image-2.0-flash",
        model_type="image",
        provider_type="openai_compatible",
        api_key_ref="AGNES_API_KEY",
        http_poll_config=None,
    )


@pytest.fixture
def video_model():
    """视频模型 agnes-video-v2.0（http_poll 异步轮询）。"""
    return SimpleNamespace(
        endpoint=AGNES_ENDPOINT,
        model_id="agnes-video-v2.0",
        model_type="video",
        provider_type="http_poll",
        api_key_ref="AGNES_API_KEY",
        http_poll_config={
            "submit_path": "/videos",
            "query_path": "/videos/{task_id}",
            "task_id_jsonpath": "$.task_id",
            "status_jsonpath": "$.status",
            "result_jsonpath": "$.metadata.url||$.video_url||$.remixed_from_video_id",
            "method": "GET",
            "headers": {},
        },
    )


@pytest.fixture
def text_model():
    """文本模型 agnes-2.0-flash。"""
    return SimpleNamespace(
        endpoint=AGNES_ENDPOINT,
        model_id="agnes-2.0-flash",
        model_type="text",
        provider_type="openai_compatible",
        api_key_ref="AGNES_API_KEY",
        http_poll_config=None,
    )


@pytest.fixture
def tts_model():
    """TTS 模型 CosyVoice3（本地服务，无 api_key）。"""
    return SimpleNamespace(
        endpoint="http://localhost:9880",
        model_id="cosyvoice3",
        model_type="tts",
        provider_type="openai_tts",
        api_key_ref="",  # 本地服务无 key
        http_poll_config=None,
    )


# ─── 环境变量（让 resolve_key 能取到 key）──────────────────────────

@pytest.fixture(autouse=True)
def _agnes_api_key(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "sk-fake-test-key-for-mock-only")
    # 确保 build_httpx_client 不走代理
    monkeypatch.setenv("HTTPS_PROXY", "")


# ─── 响应快照加载 ──────────────────────────────────────────────────

@pytest.fixture
def load_fixture():
    """加载 tests/fixtures/agnes_responses/ 下的 JSON 快照。"""
    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))
    return _load
