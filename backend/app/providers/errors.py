"""Provider 异常与中文错误映射。"""
import httpx


class ProviderError(Exception):
    def __init__(self, msg: str, code: str | None = None):
        super().__init__(msg)
        self.code = code


# 审核拦截关键词：Agnes 对正常内容存在间歇性误判（content_policy_violation，
# 四视图/关键帧链路实测"同一 prompt 偶发拦截、重试可自愈"），任务侧据此自动重试。
_POLICY_KEYWORDS = (
    "content_policy_violation",
    "content policy",
    "safety",
    "安全策略",
    "审核",
)


def is_content_policy(exc: Exception) -> bool:
    """判断异常是否属于安全策略拦截（仅此类错误才应自动重试）。

    注意：Agnes 审核拦截以 HTTP 400 返回，openai_compatible._post 的
    raise_for_status() 抛 httpx.HTTPStatusError——关键词（content_policy_violation
    等）在响应体 body 里，str(exc) 只有 "Client error '400 Bad Request'..."，
    必须额外检查 exc.response.text 才能命中。
    """
    text = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            text += "\n" + exc.response.text
        except Exception:
            pass
    low = text.lower()
    return any(kw in low for kw in _POLICY_KEYWORDS)


def is_rate_limit(exc: Exception) -> bool:
    """判断异常是否属于限流（HTTP 429）。限流为间歇性错误，应退避重试。"""
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return True
        try:
            body = exc.response.text.lower()
        except Exception:
            body = ""
        if "rate limit" in body or "too many requests" in body or "请求过于频繁" in body:
            return True
    low = str(exc).lower()
    return "429" in low or "too many requests" in low or "rate limit" in low


def map_to_chinese(exc: Exception) -> str:
    """把底层异常映射为用户可读的中文提示。"""
    if isinstance(exc, ProviderError):
        return str(exc)
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return "网络无法连接到模型服务，请检查代理或网络"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        body = exc.response.text[:300]
        low = body.lower()
        if code in (401, 403):
            return "模型密钥无效或无权限"
        if code == 429:
            return "请求过于频繁，请稍后重试"
        if code == 400:
            if "response_format" in body or "UnsupportedParam" in body:
                return "请求参数不被模型支持（如对纯文生图传了 response_format）"
            # 内容审核：prompt 触发安全策略
            if "content_policy_violation" in low or "content policy" in low or "safety" in low:
                return ("内容被模型安全策略拦截，请修改 prompt 后重试。"
                        "常见原因：含暴力/血腥/涉性/未成年/政治敏感/自残等表述。"
                        "建议：弱化或删除敏感词、调整描述角度、改用中性表达。")
            return f"请求参数错误：{body}"
        return f"模型服务返回 {code}：{body}"
    return str(exc)
