"""agent 工具族单元测试：tools/web（搜索缓存判重 / 结果格式化 / n-gram）。

真实网络搜索不测试（依赖外部服务）；覆盖纯逻辑路径：
- web_search_with_cache 的近重复查询判定与缓存回用（用 monkeypatch 替换真实搜索）
- _query_grams 字符 n-gram
- _format_search_results / _format_github_results
- 搜索缓存 TTL 清理与容量上限
"""

import time

import pytest

from app.services.agent.tools.web import (
    _format_github_results,
    _format_search_results,
    _query_grams,
    _SEARCH_CACHE_MAX,
    _search_cache,
    _search_cache_lock,
    web_search_with_cache,
)


# ── n-gram 近重复判定 ─────────────────────────────────

def test_query_grams_basic():
    assert _query_grams("古风悬疑") == {"古风", "风悬", "悬疑"}
    assert _query_grams("") == set()
    assert _query_grams("单") == {"单"}


def test_query_grams_strips_punctuation():
    assert _query_grams("古风,悬疑！") == _query_grams("古风悬疑") or set()


# ── 缓存回用（monkeypatch 掉真实搜索）─────────────────

@pytest.fixture(autouse=True)
def clean_cache():
    with _search_cache_lock:
        _search_cache.clear()
    yield
    with _search_cache_lock:
        _search_cache.clear()


def test_cache_miss_then_hit(monkeypatch):
    hits = [{"title": "T", "url": "https://a.com", "snippet": "s"}]
    monkeypatch.setattr(
        "app.services.agent.tools.web._tool_web_search",
        lambda args: hits,
    )
    # 首次：真实搜索，reused=False 且入库
    res1, reused1 = web_search_with_cache("如何做古风短剧")
    assert reused1 is False
    assert res1 == hits
    with _search_cache_lock:
        assert len(_search_cache) == 1
    # 相近查询：命中缓存，reused=True 且不再调用搜索
    res2, reused2 = web_search_with_cache("如何做古风短剧呢")
    assert reused2 is True
    assert res2 == hits


def test_cache_dissimilar_query_still_searches(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.agent.tools.web._tool_web_search",
        lambda args: (calls.append(args["query"]) or [{"title": "x", "url": "https://x.com"}]),
    )
    web_search_with_cache("玄幻修仙")
    web_search_with_cache("都市复仇短剧")  # 完全不同 → 再次真实搜索
    assert len(calls) == 2


def test_cache_capacity_bounded(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.tools.web._tool_web_search",
        lambda args: [{"title": args["query"], "url": f"https://{args['query']}.com"}],
    )
    for i in range(_SEARCH_CACHE_MAX + 5):
        web_search_with_cache(f"query-{i}")
    with _search_cache_lock:
        assert len(_search_cache) <= _SEARCH_CACHE_MAX


def test_cache_ttl_expires(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.tools.web._tool_web_search",
        lambda args: [{"title": "t", "url": "https://t.com"}],
    )
    web_search_with_cache("cache me")
    with _search_cache_lock:
        assert len(_search_cache) == 1
    monkeypatch.setattr("app.services.agent.tools.web._SEARCH_CACHE_TTL", -1.0)
    # 下一次调用：旧缓存已过期 → 清理并重新搜索
    world_now = time.monotonic()
    monkeypatch.setattr("app.services.agent.tools.web.time.monotonic", lambda: world_now + 100)
    res, reused = web_search_with_cache("cache me")
    assert reused is False
    assert res  # 仍返回结果


# ── 结果格式化 ─────────────────────────────────────────

def test_format_search_results_content():
    text = _format_search_results([
        {"title": "标题一", "url": "https://a.com/1", "snippet": "摘要"},
        {"title": "标题二", "url": "https://a.com/2"},
    ])
    assert "标题一" in text
    assert "https://a.com/1" in text
    assert "摘要" in text


def test_format_search_results_empty():
    assert "未搜索到" in _format_search_results([])


def test_format_github_results_content():
    text = _format_github_results([
        {"title": "repo-a", "url": "https://github.com/x/a", "snippet": "3 stars"},
    ])
    assert "repo-a" in text
    assert "3 stars" in text


def test_format_github_results_empty():
    assert "未在 GitHub" in _format_github_results([])


# ── 搜索校验 ───────────────────────────────────────────

def test_web_search_empty_query_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.tools.web._tool_web_search",
        lambda args: [],
    )
    with pytest.raises(ValueError, match="不能为空"):
        web_search_with_cache("   ")
