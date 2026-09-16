"""引擎内核层 · 流解析：从 OpenAI SSE chunk 提取增量（正文/思考/工具调用）。

原实现位于 agent_service.py（2026-08-11 起）；剥离为独立模块，逻辑未改动。
"""


def _extract_delta(chunk: dict) -> str:
    """从 OpenAI SSE chunk 提取文本增量。"""
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""
    except Exception:
        return ""


def _extract_reasoning(chunk: dict) -> str:
    """从 OpenAI SSE chunk 提取推理模型思考增量。

    字段兼容（2026-08-17 修复）：
    - Agnes（agnes-2.0-flash 等）：`delta.reasoning_content`
    - vLLM（deepseek-v4-flash）：`delta.reasoning`  ← 此前的盲区！
    思考阶段 content 为空；思考结束后才输出正文。思考内容仅透传给前端展示
    （thinking 事件），不进入对话正文与数据库。
    """
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        for key in ("reasoning_content", "reasoning"):
            val = delta.get(key)
            if isinstance(val, str) and val:
                return val
        return ""
    except Exception:
        return ""


def _accumulate_tool_calls(chunk: dict, acc: dict[int, dict]) -> None:
    """聚合流式 tool_calls 增量（按 index 分片累加 id/name/arguments）。"""
    choices = chunk.get("choices") or []
    if not choices:
        return
    delta = choices[0].get("delta") or {}
    for tc in delta.get("tool_calls") or []:
        idx = tc.get("index", 0)
        entry = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if tc.get("id"):
            entry["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            entry["name"] += fn["name"]
        if fn.get("arguments"):
            entry["arguments"] += fn["arguments"]
