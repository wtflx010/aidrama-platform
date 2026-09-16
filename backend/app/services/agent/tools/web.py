"""工具注册层 · 联网工具：网页抓取 / 多源搜索（Tavily+Bing+百度）/ GitHub 搜索。

从 agent_service.py 剥离（原行号 4106~4507 区域），逻辑未改动。
含近重复查询缓存回用（web_search_with_cache）。
"""

import logging
import re
import threading
import time

import httpx

logger = logging.getLogger(__name__)

_SEARCH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _tool_web_fetch(args: dict) -> str:
    """读取网页正文：抓取 URL → 提取标题 + 正文纯文本（去导航/脚本/样式）。

    对齐 TraeWork 联网搜索闭环：搜索结果只有摘要时，可调用本工具打开链接读正文。
    用正则做轻量正文提取（不引入 bs4/lxml 依赖）：优先 <article>/<main> 容器，
    否则取 <body>；去 script/style/nav 后转纯文本，压缩空白，截断防超长。
    """
    import html as _html

    from app.providers.base import build_httpx_client

    url = (args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("请输入完整的网页地址（http/https 开头）")
    client = build_httpx_client()
    try:
        r = client.get(url, headers={"User-Agent": _SEARCH_UA}, timeout=30, follow_redirects=True)
        if r.status_code >= 400:
            raise ValueError(f"网页返回 {r.status_code}，无法读取")
        html_doc = r.text
        # 标题：<title> 或 og:title
        title = ""
        t = re.search(r"<title[^>]*>(.*?)</title>", html_doc, re.S | re.I)
        if t:
            title = re.sub(r"\s+", " ", t.group(1)).strip()[:200]
        og = re.search(r'property=["\']og:title["\'][^>]*content=["\'](.*?)["\']', html_doc, re.S | re.I)
        if not title and og:
            title = og.group(1).strip()[:200]

        # 正文容器：article > main > body
        body_src = ""
        art = re.search(r"<article[^>]*>(.*?)</article>", html_doc, re.S | re.I)
        main = re.search(r"<main[^>]*>(.*?)</main>", html_doc, re.S | re.I)
        body = re.search(r"<body[^>]*>(.*?)</body>", html_doc, re.S | re.I)
        if art:
            body_src = art.group(1)
        elif main:
            body_src = main.group(1)
        elif body:
            body_src = body.group(1)
        else:
            body_src = html_doc
        # 去掉脚本/样式/导航/页脚/iframe/svg 等噪音
        for tag in ("script", "style", "nav", "header", "footer", "iframe", "noscript", "svg", "form", "button", "aside"):
            body_src = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", body_src, flags=re.S | re.I)
        # 转纯文本：块级标签换行，去其余标签，实体解码，压缩空白
        body_src = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr|/section)[^>]*>", "\n", body_src, flags=re.I)
        body_src = re.sub(r"<[^>]+>", " ", body_src)
        body_src = _html.unescape(body_src)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in body_src.split("\n")]
        text = "\n".join(ln for ln in lines if ln)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if not text:
            raise ValueError("未能从页面提取到正文内容（可能是动态渲染页面）")
        # 截断：正文最长约 6000 字，足够模型总结要点
        if len(text) > 6000:
            text = text[:6000] + "\n…（内容过长已截断）"
        out = f"网页标题：{title or url}\n来源：{url}\n\n{text}"
        return out[:8000]
    except Exception as e:
        # 网络层连接错误（超时/无法访问）：明确指向目标网站，避免被误报成模型服务故障
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError)):
            raise ValueError(
                f"无法访问目标网站（连接超时或网络不可达）：{url}\n"
                "提示：该网站可能被屏蔽、需要代理，或当前网络不稳定；可改用 web_search 搜索相关信息。"
            ) from e
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"读取网页失败：{e}") from e
    finally:
        client.close()


def _tool_web_search(args: dict) -> list[dict]:
    """联网搜索（多源合并，对齐 TraeWork 多源信息整合）：
    Tavily（配置了 SEARCH_API_KEY 时启用，质量最高）> Bing 国内版 + 百度 合并去重。

    返回按来源先后排序、已去重（同 URL / 同标题）的结果列表。
    """
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("搜索关键词不能为空")

    from app.config import settings

    sources: list[list[dict]] = []
    if settings.search_api_key:
        sources.append(_search_tavily(query))
    else:
        # Bing 与百度并行抓取（每个最多 25s 超时），省一半串行等待时间
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_bing = pool.submit(_search_bing, query)
            f_baidu = pool.submit(_search_baidu, query)
            sources = [f_bing.result(timeout=30) or [], f_baidu.result(timeout=30) or []]
    merged: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for group in sources:
        for item in group:
            url = (item.get("url") or "").strip().rstrip("/")
            title = (item.get("title") or "").strip()
            if not url and not title:
                continue
            key_url = url.lower() or title.lower()
            key_title = title.lower()
            if key_url in seen_urls or key_title in seen_titles:
                continue
            seen_urls.add(key_url)
            seen_titles.add(key_title)
            merged.append(item)
        if len(merged) >= 8:
            break
    return merged[:8]


# ── 近重复查询回用（防「一句话搜个没完 + 一串相同结果卡」）────────
# 对齐 dsh web 的检索体验：同一轮对话内模型反复用相近 query 搜索时，直接复用
# 最近 10 分钟内的缓存结果，并在结果里附「无需重复搜索」提示，省时且杜绝重复检索。
_SEARCH_CACHE_MAX = 16
_SEARCH_CACHE_TTL = 600.0
_search_cache: list[dict] = []
_search_cache_lock = threading.Lock()


def _query_grams(text: str, n: int = 2) -> set[str]:
    """字符 n-gram（中文友好），用于近重复查询判定。"""
    t = re.sub(r"[\s\"'“”（）()！!？?。，,、·\-_]+", "", (text or "").lower())
    if len(t) <= n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def web_search_with_cache(query: str, limit: int | None = None) -> tuple[list[dict], bool]:
    """联网搜索（带近重复查询缓存回用）。

    与最近 _SEARCH_CACHE_TTL 秒内相近的查询（2-gram Jaccard ≥ 0.55）命中时，
    直接复用其结果并返回 reused=True（调用方/模型侧会收到「结果已复用、勿重复搜索」）；
    未命中则真实执行多源搜索并入库。网络请求在锁外执行，避免阻塞其他搜索。
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("搜索关键词不能为空")
    now = time.monotonic()
    qg = _query_grams(query)
    with _search_cache_lock:
        _search_cache[:] = [e for e in _search_cache if now - e["ts"] < _SEARCH_CACHE_TTL]
        # 从最新条目开始匹配（模型惯于逐步改词换 query，阈值取 0.42 可覆盖其重试模式）
        for e in reversed(_search_cache):
            eg = _query_grams(e["query"])
            if not qg or not eg:
                continue
            inter = len(qg & eg)
            union = len(qg | eg)
            if union and inter / union >= 0.42:
                return list(e["results"]), True
    results = _tool_web_search({"query": query})
    with _search_cache_lock:
        _search_cache.append({"query": query, "results": results, "ts": time.monotonic()})
        if len(_search_cache) > _SEARCH_CACHE_MAX:
            _search_cache.pop(0)
    return results, False


def _tool_github_search(args: dict) -> list[dict]:
    """GitHub 仓库搜索（GitHub Search API，未认证限 10 次/分钟）。

    返回仓库 full_name / stars / language / description，供模型整理回答。
    搜索失败（限流/网络）时抛 ValueError，由工具执行器转成失败结果。
    """
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("搜索关键词不能为空")

    from app.providers.base import build_httpx_client
    client = build_httpx_client()
    try:
        r = client.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": 8},
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ai-creation-assistant",
            },
            timeout=25,
        )
        if r.status_code == 403:
            raise ValueError("GitHub 搜索触发限流（未认证每小时/分钟配额），请稍后再试")
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
        return [
            {
                "title": (it.get("full_name") or "").strip(),
                "url": (it.get("html_url") or "").strip(),
                "snippet": (
                    f"{it.get('stargazers_count') or 0} stars · "
                    f"{it.get('language') or 'N/A'} · "
                    f"{(it.get('description') or '').strip()[:160]}"
                ),
            }
            for it in items
        ]
    finally:
        client.close()


def _format_github_results(results: list[dict]) -> str:
    if not results:
        return "未在 GitHub 上搜索到相关仓库"
    lines = []
    for i, r in enumerate(results):
        line = f"{i + 1}. {r['title']}\n   {r['url']}"
        if r.get("snippet"):
            line += f"\n   {r['snippet']}"
        lines.append(line)
    return "GitHub 搜索结果：\n" + "\n".join(lines)


def _search_tavily(query: str, max_results: int = 5) -> list[dict]:
    """Tavily 搜索（需 SEARCH_API_KEY，免费额度）。"""
    from app.config import settings
    from app.providers.base import build_httpx_client
    client = build_httpx_client()
    try:
        r = client.post(
            "https://api.tavily.com/search",
            json={"api_key": settings.search_api_key, "query": query, "max_results": max_results},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        results: list[dict] = []
        for item in (data.get("results") or []):
            results.append({
                "title": (item.get("title") or "").strip(),
                "url": (item.get("url") or "").strip(),
                "snippet": (item.get("content") or "").strip()[:200],
            })
        return results
    finally:
        client.close()


def _search_bing(query: str) -> list[dict]:
    """cn.bing.com 国内版搜索兜底（无需 API Key）。

    解析 b_algo 结果块：优先取 <h2><a>…</a></h2> 作标题（Bing 新版标题含
    域名面包屑，需清洗），href 过滤掉 bing/microsoft 跳转链接；摘要取 b_caption。
    """
    import urllib.parse

    from app.providers.base import build_httpx_client
    client = build_httpx_client()
    try:
        url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}"
        r = client.get(url, headers={"User-Agent": _SEARCH_UA}, timeout=25)
        r.raise_for_status()
        html = r.text
        blocks = re.findall(r'<li class="b_algo".*?</li>', html, re.S)
        results: list[dict] = []
        for b in blocks:
            # 标题/链接：优先 <h2><a>…</a></h2>（Bing 正文标题，含面包屑导航时取最后一个 › 之后）
            title = ""
            href = ""
            h2 = re.search(r"<h2[^>]*>(.*?)</h2>", b, re.S)
            h2_links = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', h2.group(1), re.S) if h2 else []
            for a_href, a_txt in h2_links:
                if "bing.com" in a_href or "microsoft.com" in a_href:
                    continue
                txt = re.sub(r"<[^>]+>", "", a_txt).strip()
                if txt:
                    href, title = a_href, txt
                    break
            if not title:
                # 回退：非跳转链接的最后一个 › 之后（跳过面包屑前缀，如 "github.comhttps://github.com › MiniMax-AI"）
                links = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', b, re.S)
                for a_href, a_txt in links:
                    if "bing.com" in a_href or "microsoft.com" in a_href:
                        continue
                    txt = re.sub(r"<[^>]+>", "", a_txt).strip()
                    if txt:
                        href = a_href
                        title = txt.split("https://")[-1].split("http://")[-1]
                        if " › " in title:
                            title = title.split(" › ")[-1]
                        break
            if not title:
                continue
            # HTML 实体解码（&#183; › &#47; 等）
            try:
                import html as _html
                title = _html.unescape(title)
            except Exception:
                pass
            title = re.sub(r"^//[a-z0-9.-]+(\s*›\s*|\s*:\s*)?", "", title).strip()
            title = re.sub(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(\s*›\s*)?", "", title).strip()
            title = title.split("https://")[0].split("http://")[0].strip(" ›|: ")
            # 抓摘要（b_caption 段落）补充信息量
            snippet = ""
            cap = re.search(r'<div class="b_caption"[^>]*>(.*?)</div>', b, re.S)
            if cap:
                p = re.search(r"<p[^>]*>(.*?)</p>", cap.group(1), re.S)
                if p:
                    snippet = re.sub(r"<[^>]+>", "", p.group(1)).strip()
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet[:200],
            })
            if len(results) >= 5:
                break
        return results
    finally:
        client.close()


def _search_baidu(query: str, max_results: int = 5) -> list[dict]:
    """百度搜索（www.baidu.com 无 Key 兜底，与 Bing 合并多源，去重交给 _tool_web_search）。

    百度移动版（m.baidu.com/s?word=）反爬较轻、HTML 更规整，解析结果块：
    标题/链接取 result 块内首个非跳转 <a>（优先 mu= 真实地址属性），摘要取 c-abstract。
    """
    import urllib.parse

    from app.providers.base import build_httpx_client
    client = build_httpx_client()
    try:
        url = f"https://m.baidu.com/s?word={urllib.parse.quote(query)}&ie=utf-8"
        r = client.get(
            url,
            headers={"User-Agent": _SEARCH_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=25,
        )
        r.raise_for_status()
        html = r.text
        # 结果块：<div class="result"…> 或 <div class="c-container"…>
        blocks = re.findall(r'<div class="(?:result|c-container)"[^>]*>.*?(?=<div class="(?:result|c-container)")', html, re.S)
        results: list[dict] = []
        for b in blocks:
            # 真实链接优先取 mu 属性（百度反爬会把 href 改为跳转），否则取首个外链
            href = ""
            mu = re.search(r'\bmu="(https?://[^"]+)"', b)
            if mu:
                href = mu.group(1)
            else:
                links = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>', b)
                for l in links:
                    if "baidu.com" in l and "www.baidu.com/link" not in l:
                        continue
                    href = l
                    break
            # 标题：首个 <h3><a>…</a></h3>
            title = ""
            h3 = re.search(r"<h3[^>]*>\s*<a[^>]*>(.*?)</a>", b, re.S)
            if h3:
                title = re.sub(r"<[^>]+>", "", h3.group(1)).strip()
            if not title:
                title = re.search(r"<h3[^>]*>(.*?)</h3>", b, re.S)
                title = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else ""
            if not title:
                continue
            try:
                import html as _html
                title = _html.unescape(title)
            except Exception:
                pass
            title = re.sub(r"<[^>]+>", "", title).strip()
            # 摘要：c-abstract 块
            snippet = ""
            abs_ = re.search(r'class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</', b, re.S)
            if abs_:
                snippet = re.sub(r"<[^>]+>", "", abs_.group(1)).strip()
            if not snippet:
                txt = re.sub(r"<[^>]+>", " ", b)
                snippet = txt.strip()[:160]
            results.append({"title": title[:120], "url": href, "snippet": snippet[:200]})
            if len(results) >= max_results:
                break
        return results
    except Exception:
        # 百度偶尔返回验证页/反爬，静默降级（Bing 兜底仍可用）
        return []
    finally:
        client.close()


def _format_search_results(results: list[dict]) -> str:
    if not results:
        return "未搜索到相关结果"
    lines = []
    for i, r in enumerate(results):
        line = f"{i + 1}. {r['title']}\n   {r['url']}"
        if r.get("snippet"):
            line += f"\n   {r['snippet']}"
        lines.append(line)
    return "搜索结果：\n" + "\n".join(lines)
