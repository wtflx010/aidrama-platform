"""引擎内核层 · 事件辅助：SSE 事件编码。

SSE 事件格式（data: {json}）：
  {"type": "token",    "content": "..."}   流式文本增量
  {"type": "thinking", "content": "..."}  思考增量
  {"type": "tool",     "name": "...", "status": "running|succeeded|failed|pending"}
  {"type": "media",    "url": "...", "kind": "image|audio|file"}
  {"type": "project",  "id": "...", "title": "..."}
  {"type": "done",     "message_id": "...", "context_tokens": N}
  {"type": "error",    "message": "..."}
"""


def _sse(obj: dict) -> str:
    import json
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
