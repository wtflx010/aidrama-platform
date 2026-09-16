"""限流（429）判定与设计图 429 重试测试。"""
import httpx

from app.providers.errors import is_rate_limit, map_to_chinese


def _make_429():
    req = httpx.Request("POST", "https://apihub.agnes-ai.cn/v1/images/generations")
    resp = httpx.Response(429, request=req, json={"error": {"message": "rate limit exceeded"}})
    return httpx.HTTPStatusError("Client error '429 Too Many Requests'", request=req, response=resp)


def test_is_rate_limit_429_status():
    """HTTP 429 状态码应命中限流判定。"""
    assert is_rate_limit(_make_429())


def test_is_rate_limit_body_keywords():
    """非 429 但响应体含限流关键词也应命中。"""
    req = httpx.Request("POST", "https://apihub.agnes-ai.cn/v1/images/generations")
    resp = httpx.Response(400, request=req, json={"error": {"message": "too many requests"}})
    err = httpx.HTTPStatusError("Client error '400 Bad Request'", request=req, response=resp)
    assert is_rate_limit(err)


def test_is_rate_limit_negative():
    """审核拦截 / 普通错误不应误判为限流。"""
    assert not is_rate_limit(httpx.HTTPStatusError(
        "500", request=_make_429().request,
        response=httpx.Response(500, request=_make_429().request),
    ))
    assert not is_rate_limit(ValueError("content_policy_violation"))


def test_map_to_chinese_429():
    assert "过于频繁" in map_to_chinese(_make_429())
