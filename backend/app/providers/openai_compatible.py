"""OpenAI 兼容适配器：文本 + 文生图 + 图生图（Agnes 同步返回）。"""
import logging
import time
from uuid import uuid4

import httpx

from app.providers.base import (
    BaseProvider,
    ImageOpts,
    ProviderStatus,
    TaskHandle,
    TaskResult,
)
from app.providers.errors import ProviderError, map_to_chinese
from app.utils.media import clean_remote_url, media_url_to_data_uri

logger = logging.getLogger(__name__)

# Agnes 图片队列满（503 image queue is full）：服务端队列临时打满，提交级自动退避重试，
# 避免批量生成时任务直接失败。间隔递增，总等待约 5 分钟（与 http_poll 视频队列一致）。
_QUEUE_FULL_MAX_RETRIES = 5
_QUEUE_FULL_BACKOFF = (20, 40, 80, 160)

# 思考贪婪型推理模型的生成预算上限（token）与思考抑制指令。
# 背景（2026-08-17 排查）：deepseek-v4-flash 等推理模型在 vLLM 上，思考(reasoning)
# 与正文共享 max_tokens 预算且思考贪婪占满——不加上限时模型把数分钟/数千 token
# 花在隐式推理上、正文长期不出（实测 51s / 0 字正文 / 4940 字思考）。注入
# system 抑制指令 + 预算上限后，同一请求 32.7s / 1976 字正文 / 147 字思考。
# 注意：system 抑制对简单任务有效，但对复杂结构化任务（长 JSON schema 指令）
# 模型仍会无视指令大量思考（实测 86s / 0 正文 / 6325 思考）。根层修复是 vLLM 原生
# max_reasoning_tokens —— 精确限制思考 token，正文预算不受影响
# （实测 max_reasoning_tokens=300 → 25.6s / 3332 字正文 / 245 思考）。
# 该控制是【请求级】：只影响本后端发给模型服务的请求体，不影响其他进程。
_THINKING_MODEL_MAX_TOKENS = 4000
_THINKING_MODEL_MAX_REASONING = 300
_THINKING_SUPPRESS_PROMPT = (
    "请直接、完整地输出最终成果。不要输出任何思考过程/推理草稿"
    "（不要输出 reasoning 内容），把全部生成预算用于正文本身。"
)
# 命中即视为思考贪婪型：vLLM 常用推理模型 id 特征
_THINKING_MODEL_MARKERS = ("deepseek", "v3", "v4-flash", "r1", "reason")


def _is_queue_full(resp: httpx.Response) -> bool:
    """识别 Agnes 图片队列打满（503 + error.message 含 "queue is full"）。"""
    if resp.status_code != 503:
        return False
    try:
        data = resp.json()
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    msg = ((data.get("error") or {}).get("message") or "") if isinstance(data.get("error"), dict) else ""
    return "queue is full" in msg.lower()


class OpenAICompatibleProvider(BaseProvider):
    provider_type = "openai_compatible"

    def _post(self, url: str, body: dict, timeout: float, retries: int = 2):
        """带重试的 POST。

        - Agnes 图片队列满（503 + "image queue is full"）：队列打满属瞬时状态，
          走专用指数退避重试（最多 5 次，20→160s，总约 5 分钟），耗尽后抛中文提示
        - 5xx（Cloudflare 网关 520/502/503/504）与网络错误：瞬时故障，重试可自愈，
          间隔 2s → 4s 线性退避
        - 4xx（400/401/403/429）不重试——参数错误重试无意义，429 重试会加重限流
        """
        last_exc: Exception | None = None
        qf_attempt = 0
        for attempt in range(max(retries, _QUEUE_FULL_MAX_RETRIES) + 1):
            try:
                r = self.http.post(url, headers=self._auth(), json=body, timeout=timeout)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
            # 图片队列满：指数退避重试（不消耗通用重试次数）
            if r.status_code == 503 and _is_queue_full(r):
                if qf_attempt < _QUEUE_FULL_MAX_RETRIES - 1:
                    wait = _QUEUE_FULL_BACKOFF[min(qf_attempt, len(_QUEUE_FULL_BACKOFF) - 1)]
                    logger.warning(
                        "图片队列已满（503 image queue is full），%s 秒后自动重试（第 %d/%d 次）",
                        wait, qf_attempt + 1, _QUEUE_FULL_MAX_RETRIES,
                    )
                    time.sleep(wait)
                    qf_attempt += 1
                    continue
                raise ProviderError(
                    "图片生成队列已满（image queue is full），已自动重试多次仍未成功，请稍后再试"
                )
            if r.status_code >= 500 and attempt < retries:
                last_exc = httpx.HTTPStatusError(
                    f"模型服务返回 {r.status_code}（网关瞬时错误），准备重试",
                    request=r.request, response=r,
                )
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        assert last_exc is not None
        raise last_exc

    def _needs_thinking_budget(self) -> bool:
        """当前模型是否是需要思考预算控制的推理模型（按 model_id 特征判定）。"""
        mid = (self.cfg.model_id or "").lower()
        return any(m in mid for m in _THINKING_MODEL_MARKERS)

    def _maybe_suppress_thinking(
        self, messages: list[dict], suppress_thinking: bool | None = None,
    ) -> list[dict]:
        """按需在 messages 首条 system 前注入思考抑制指令。

        suppress_thinking 显式传入时以调用方为准；None 时按模型特征自动判定
        （仅对思考贪婪型推理模型注入，普通模型不加任何东西）。
        """
        if not self._needs_thinking_budget():
            return messages
        if suppress_thinking is False:
            return messages
        sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), None)
        inject = {"role": "system", "content": _THINKING_SUPPRESS_PROMPT}
        if sys_idx is None:
            return [inject, *messages]
        out = list(messages)
        out[sys_idx] = {
            **out[sys_idx],
            "content": f"{out[sys_idx]['content']}\n\n{_THINKING_SUPPRESS_PROMPT}",
        }
        return out

    def _apply_thinking_controls(self, body: dict, max_tokens: int | None,
                                 suppress_thinking: bool | None) -> dict:
        """对思考贪婪型推理模型统一加预算控制（请求级）。

        - max_tokens：正文生成预算上限（调用方传入优先，否则用默认）
        - max_reasoning_tokens：限制思考 token（vLLM 原生参数），复杂任务下
          system 抑制无效时仍能保证正文产出；普通模型不加
        - suppress_thinking=True 且模型支持：额外在请求体加
          chat_template_kwargs.thinking=false 覆盖服务端默认
          (thinking:true)，从源头关掉思考，只走正文（实测 0.9s 出纯 JSON，
          远快于走 reasoning 的几十秒）。若端到端被随默认拒绝/无视，回退由
          max_reasoning_tokens 限思考（现有机制）。
        """
        if not self._needs_thinking_budget():
            return body
        if max_tokens is not None or "max_tokens" not in body:
            body["max_tokens"] = max_tokens or _THINKING_MODEL_MAX_TOKENS
        if suppress_thinking is not False:
            body["max_reasoning_tokens"] = _THINKING_MODEL_MAX_REASONING
            # 全面关闭思考：覆盖模板默认（服务端 --default-chat-template-kwargs
            # thinking:true, effort:max）。此参数对 vLLM deepseek_v4 模板有效；
            # 若不支持该 key，vLLM 会忽略未知 key，仍有 max_reasoning_tokens 兜底。
            body.setdefault("chat_template_kwargs", {}).update({
                "thinking": False,
                "reasoning_effort": "off",
            })
        return body

    def chat(self, messages: list[dict], stream: bool = False, max_tokens: int | None = None,
             suppress_thinking: bool | None = None) -> dict:
        url = f"{self.cfg.endpoint}/chat/completions"
        body: dict = {"model": self.cfg.model_id, "stream": stream}
        body["messages"] = self._maybe_suppress_thinking(messages, suppress_thinking)
        self._apply_thinking_controls(body, max_tokens, suppress_thinking)
        # 推理模型思考耗时较长，30s 会超时，统一放宽到 180s
        r = self._post(url, body, timeout=180)
        return r.json()

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                    max_tokens: int | None = None, suppress_thinking: bool | None = None):
        """流式对话（创作助手 SSE）：逐 chunk 产出 OpenAI SSE 事件 dict。

        - 支持 tools（OpenAI 兼容 function calling）参数，工具调用在流式 chunk 中返回
        - 网络/服务错误：抛 ProviderError，由调用方转 SSE error 事件
        - max_tokens / suppress_thinking：对「思考贪婪型」推理模型（deepseek v3/v4 等）
          自动追加生成预算与思考 token 上限（vLLM max_reasoning_tokens），
          防止思考耗尽预算导致正文长期不出
        """
        import json

        url = f"{self.cfg.endpoint}/chat/completions"
        body: dict = {"model": self.cfg.model_id, "stream": True}
        body["messages"] = self._maybe_suppress_thinking(messages, suppress_thinking)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        self._apply_thinking_controls(body, max_tokens, suppress_thinking)
        try:
            with self.http.stream("POST", url, headers=self._auth(), json=body, timeout=180) as r:
                if r.status_code >= 400:
                    detail = r.text[:300]
                    raise ProviderError(f"对话模型返回 {r.status_code}: {detail}")
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            raise ProviderError(f"对话模型连接失败: {e}")

    def textToImage(self, prompt: str, opts: ImageOpts) -> TaskHandle:
        url = f"{self.cfg.endpoint}/images/generations"
        body: dict = {"model": self.cfg.model_id, "prompt": prompt, "n": opts.n}
        if opts.size:
            body["size"] = opts.size
        if opts.ratio:
            body["ratio"] = opts.ratio
        # negative_prompt：Agnes 文档未列出但实测支持，用于排除背景等不想要元素
        if opts.negative_prompt:
            body["negative_prompt"] = opts.negative_prompt
        # 踩坑#1：纯文生图(agnes-image-2.1-flash)绝不传 extra_body / response_format
        # Agnes 2K 图生成耗时可达 2~3 分钟，timeout 放宽到 240s
        r = self._post(url, body, timeout=240)
        data = r.json()
        urls: list[str] = []
        for d in data.get("data", []) or []:
            if d.get("url"):
                urls.append(clean_remote_url(d["url"]))
            elif d.get("b64_json"):
                # OpenAI DALL·E 3 / gpt-image-1 等官方接口默认返回 base64（b64_json），
                # 直接落盘 media 目录并返回本地 URL，与下游（关键帧落库/agent 工具）兼容。
                # 惰性引入避免 provider → tasks 循环依赖。
                from app.tasks.base import bytes_to_local
                urls.append(bytes_to_local(
                    d["b64_json"], subdir="gen/openai-gen",
                    filename=f"img_{uuid4().hex[:12]}.png",
                ))
        result = TaskResult(status=ProviderStatus.succeeded, imageUrls=urls, raw=data)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=f"sync-{uuid4().hex}",
            meta={"result": result.model_dump(mode="json")},
        )

    def imageToImage(self, prompt: str, image_urls: list[str], opts: ImageOpts) -> TaskHandle:
        """图生图/多图合成：以 image_urls（场景/角色/道具参考图）为参考生成新图。

        踩坑#1：图生图(agnes-image-2.0-flash)必须传 extra_body={image:[urls], response_format:"url"}
        踩坑#2：Agnes 不接受 localhost/私有网络 URL，需把本地 media URL 转 base64 data URI
        多图：agnes-image-2.0-flash 原生支持多图合成（image 数组），
        场景+角色+道具全部作为参考图，由模型按 prompt 组合成关键帧。
        """
        image_urls = [media_url_to_data_uri(u) for u in image_urls]
        url = f"{self.cfg.endpoint}/images/generations"
        body: dict = {
            "model": self.cfg.model_id,
            "prompt": prompt,
            "n": opts.n,
            "extra_body": {"image": image_urls, "response_format": "url"},
        }
        if opts.size:
            body["size"] = opts.size
        if opts.ratio:
            body["ratio"] = opts.ratio
        # negative_prompt：img2img 同样支持，用于压过参考图的背景倾向
        if opts.negative_prompt:
            body["negative_prompt"] = opts.negative_prompt
        # 图生图同样耗时较长，放宽到 240s
        r = self._post(url, body, timeout=240)
        data = r.json()
        urls: list[str] = []
        for d in data.get("data", []) or []:
            if d.get("url"):
                urls.append(clean_remote_url(d["url"]))
            elif d.get("b64_json"):
                from app.tasks.base import bytes_to_local
                urls.append(bytes_to_local(
                    d["b64_json"], subdir="gen/openai-gen",
                    filename=f"img_{uuid4().hex[:12]}.png",
                ))
        result = TaskResult(status=ProviderStatus.succeeded, imageUrls=urls, raw=data)
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=f"sync-{uuid4().hex}",
            meta={"result": result.model_dump(mode="json")},
        )

    def getTaskResult(self, handle: TaskHandle) -> TaskResult:
        if "result" in handle.meta:
            return TaskResult(**handle.meta["result"])
        return TaskResult(status=ProviderStatus.succeeded)

    def test_connection(self) -> tuple[bool, str]:
        if self.cfg.model_type == "text":
            try:
                self.chat([{"role": "user", "content": "ping"}])
                return True, "OK"
            except Exception as e:
                return False, map_to_chinese(e)
        if self.cfg.model_type == "image":
            try:
                h = self.textToImage("connectivity test", ImageOpts(n=1))
                urls = h.meta["result"].get("imageUrls", [])
                if urls:
                    return True, f"OK {urls[0][:60]}"
                return True, "OK(无URL)"
            except Exception as e:
                return False, map_to_chinese(e)
        return False, "不支持的 model_type"
