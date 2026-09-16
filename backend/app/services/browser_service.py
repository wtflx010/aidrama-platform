"""浏览器自动化（对齐 TraeWork agent-browser）：headless Chromium 网页操作。

- 全局单例会话，模型通过 browser_navigate / browser_snapshot / browser_click /
  browser_type / browser_screenshot 组合完成网页操作（打开页面、点击、填表、截图）。
- 可交互元素用「索引编号」定位：snapshot 输出编号清单，click/type 按编号操作，
  避免模型自行构造 CSS selector 出错。
- 会话 5 分钟无操作自动关闭；浏览器启动失败（未安装 chromium）返回明确提示，不阻塞对话。
"""
import threading
import time
import uuid
from pathlib import Path

from app.config import settings

_IDLE_TIMEOUT = 300  # 5 分钟无操作自动关闭浏览器
_MAX_SNAPSHOT = 2500  # snapshot 输出最大字符
_NAV_TIMEOUT = 30000  # 页面加载超时 ms

_INTERACTIVE_SELECTOR = "a[href], button, input, textarea, select, [role=button], [role=link], [role=textbox]"


def _find_system_browser() -> str | None:
    """查找系统已安装的 Chromium 系浏览器可执行文件（macOS）。"""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


class BrowserSession:
    """单例浏览器会话：跨工具调用保持同一页面；惰性启动、空闲自动关闭。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._p = None          # Playwright
        self._browser = None    # Browser
        self._page = None       # Page
        self._elements: list[dict] = []  # 最近一次 snapshot 的可交互元素
        self._last_used = 0.0
        self._launch_error: str | None = None

    # ─── 生命周期 ────────────────────────────────
    def _ensure(self):
        """惰性启动浏览器（幂等）。启动失败缓存错误信息，避免反复尝试。"""
        if self._page is not None:
            self._touch()
            return
        if self._launch_error:
            raise RuntimeError(self._launch_error)
        from playwright.sync_api import sync_playwright

        try:
            self._p = sync_playwright().start()
            # 优先复用系统 Chrome/Chromium/Edge（executable_path 直达，免下载浏览器）；
            # 未安装时回退 playwright 自带 chromium
            exe = _find_system_browser()
            if exe:
                self._browser = self._p.chromium.launch(
                    headless=True,
                    executable_path=exe,
                    args=["--disable-blink-features=AutomationControlled"],
                )
            else:
                self._browser = self._p.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
            context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                locale="zh-CN",
            )
            self._page = context.new_page()
        except Exception as e:  # noqa: BLE001
            self._launch_error = (
                "浏览器启动失败，请确认已安装 Chrome/Chromium/Edge，或在 backend 目录执行 "
                "`./venv/bin/python -m playwright install chromium`。"
                f"（{e}）"
            )
            self._close_raw()
            raise RuntimeError(self._launch_error)
        self._touch()

    def _touch(self):
        self._last_used = time.time()

    def _close_raw(self):
        for obj in (self._browser, self._p):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._browser = self._p = self._page = None

    def close(self):
        with self._lock:
            self._close_raw()
            self._elements = []

    def _idle_expired(self) -> bool:
        return self._page is not None and time.time() - self._last_used > _IDLE_TIMEOUT

    # ─── 页面操作 ────────────────────────────────
    def navigate(self, url: str) -> str:
        url = (url or "").strip()
        # 本地 HTML 文件（file:// 或本地绝对路径）直接放行；其余无协议头时补 https://
        if not url.startswith(("http://", "https://", "file://")):
            if url.startswith("/") and Path(url).exists():
                url = "file://" + url
            else:
                url = "https://" + url
        with self._lock:
            self._ensure()
            try:
                self._page.goto(url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                # 超时/网络错误：明确标记失败（run() 据此返回 ok=False），
                # 让模型知道浏览器方式不可行，改走其他途径（换 raw 直链/搜索等）
                self._touch()
                return (
                    f"[页面加载失败] 无法打开 {url}：{e}\n"
                    f"{self._snapshot_locked()}"
                )
            # 即便 goto 未抛异常，也可能落入 Chrome 错误页（被屏蔽/DNS 失败/ERR_TIMED_OUT）
            try:
                if self._page.url.startswith("chrome-error://"):
                    self._touch()
                    return (
                        f"[页面加载失败] 目标网站无法访问（浏览器落入错误页，可能被屏蔽或需要代理）：{url}\n"
                        f"{self._snapshot_locked()}"
                    )
            except Exception:
                pass
            return self._snapshot_locked()

    def snapshot(self) -> str:
        with self._lock:
            self._ensure()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> str:
        """快照当前页面（调用方须已持有 self._lock）。"""
        self._collect_elements()
        title = ""
        url = ""
        try:
            title = (self._page.title() or "").strip()
            url = self._page.url
        except Exception:
            pass
        lines = [f"页面：{title}", f"地址：{url}", ""]
        if not self._elements:
            lines.append("（未检测到可交互元素）")
        for el in self._elements:
            tag = el["tag"]
            text = el.get("text") or ""
            lines.append(f"[{el['index']}] <{tag}> {text}")
        # 正文摘要
        try:
            body = self._page.locator("body").inner_text(timeout=3000) or ""
            body = " ".join(x.strip() for x in body.splitlines() if x.strip())
            if body:
                lines.append("")
                lines.append("正文：" + body[: _MAX_SNAPSHOT - sum(len(x) + 1 for x in lines)])
        except Exception:
            pass
        out = "\n".join(lines)
        return out[: _MAX_SNAPSHOT]

    def click(self, index) -> str:
        with self._lock:
            self._ensure()
            self._collect_elements()
            el = self._find(index)
            try:
                el.scroll_into_view_if_needed(timeout=3000)
                el.click(timeout=5000)
            except Exception as e:  # noqa: BLE001
                return f"点击 [第 {index} 个元素] 失败：{e}\n\n{self._snapshot_locked()}"
            time.sleep(0.8)  # 等待页面响应
            return self._snapshot_locked()

    def type_text(self, index, text: str) -> str:
        with self._lock:
            self._ensure()
            self._collect_elements()
            el = self._find(index)
            tag = el.evaluate("(n) => n.tagName.toLowerCase()")
            try:
                if tag in ("select",):
                    el.select_option(label=text)
                else:
                    el.click(timeout=5000)
                    el.fill("", timeout=3000)
                    el.fill(text, timeout=3000)
            except Exception as e:  # noqa: BLE001
                return f"输入 [第 {index} 个元素] 失败：{e}\n\n{self._snapshot_locked()}"
            return f"已输入「{text}」到 [第 {index} 个元素]（<{tag}>）。\n\n" + self._snapshot_locked()

    def screenshot(self) -> dict:
        """截图保存到 media 目录，返回 {url, path}；失败返回 {error}。"""
        with self._lock:
            self._ensure()
            try:
                save_dir = Path(settings.media_dir) / "browser"
                save_dir.mkdir(parents=True, exist_ok=True)
                name = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
                path = save_dir / name
                self._page.screenshot(path=str(path), full_page=True)
            except Exception as e:  # noqa: BLE001
                return {"error": f"截图失败：{e}"}
            url = f"{settings.static_base_url}/media/browser/{name}"
            return {"url": url, "path": str(path)}

    # ─── 内部：元素收集 / 定位 ────────────────────
    def _collect_elements(self):
        """收集当前页可见可交互元素（去重，标签+文本摘要）。"""
        self._elements = []
        seen = set()
        try:
            locators = self._page.locator(_INTERACTIVE_SELECTOR).all()
        except Exception:
            return
        for loc in locators:
            try:
                if not loc.is_visible(timeout=1000):
                    continue
                tag = loc.evaluate("(n) => n.tagName.toLowerCase()")
                raw = (loc.inner_text(timeout=1000) or "").strip().replace("\n", " ")
                text = raw[:60]
                key = (tag, text)
                if key in seen:
                    continue
                seen.add(key)
                self._elements.append({"index": len(self._elements), "tag": tag, "text": text, "loc": loc})
            except Exception:
                continue

    def _find(self, index):
        index = int(index)
        if not 0 <= index < len(self._elements):
            raise ValueError(
                f"元素编号 {index} 不存在（当前共 {len(self._elements)} 个可交互元素）。"
                "请先调用 browser_snapshot 获取最新编号。"
            )
        return self._elements[index]["loc"]


# 全局单例
_session = BrowserSession()
_session_lock = threading.Lock()


def is_available() -> bool:
    """能力可用性：chromium 已安装才注册浏览器工具（由调用方决定是否返回提示）。"""
    return True


def run(action: str, args: dict) -> dict:
    """浏览器工具统一入口：action ∈ navigate/snapshot/click/type/screenshot。"""
    from playwright.sync_api import Error as PWError

    try:
        if action == "navigate":
            text = _session.navigate(args.get("url") or "")
            # 页面加载失败（超时/错误页）→ 标记 ok=False，让模型换其他途径
            if text.startswith("[页面加载失败]"):
                return {"ok": False, "text": text}
            return {"ok": True, "text": text}
        if action == "snapshot":
            return {"ok": True, "text": _session.snapshot()}
        if action == "click":
            return {"ok": True, "text": _session.click(args.get("index"))}
        if action == "type":
            return {"ok": True, "text": _session.type_text(args.get("index"), args.get("text") or "")}
        if action == "screenshot":
            result = _session.screenshot()
            if "error" in result:
                return {"ok": False, "text": result["error"]}
            return {"ok": True, "text": f"已截图：{result['url']}", "url": result["url"]}
        return {"ok": False, "text": f"未知浏览器操作：{action}"}
    except (RuntimeError, ValueError) as e:
        return {"ok": False, "text": str(e)}
    except PWError as e:
        return {"ok": False, "text": f"浏览器操作失败：{e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": f"浏览器操作异常：{e}"}


def shutdown():
    """进程退出时关闭浏览器（可选调用，不强依赖）。"""
    with _session_lock:
        try:
            _session.close()
        except Exception:
            pass
