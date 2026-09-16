"""引擎内核层 · 通用解析工具（从 LLM 输出提取结构化数据）。"""

import re


def _parse_json_flexible(raw: str) -> dict:
    """从 LLM 输出中提取 JSON：优先整体解析，失败则剥去代码围栏/前后杂文字再试。"""
    import json

    text = (raw or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
