"""MCP 服务器接入服务（创作助手可调用外部工具，2026-08-11）。

支持 stdio 本地进程 MCP 服务器：
- list_tools()：连接服务器并发现其工具，转换为 OpenAI function 定义（工具名 mcp__<server>__<tool>）
- call_tool()：调用 MCP 工具并返回结果文本
- 工具发现结果进程内缓存（TTL 60s），避免每次对话都重启子进程
"""
import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# 工具发现缓存：{server_id: (expire_ts, [tool_def])}
_discovery_cache: dict[str, tuple[float, list[dict]]] = {}
_DISCOVERY_TTL = 60.0
# 单次工具调用超时（秒）：MCP 服务器可能在思考/搜索，放宽到 120s
_CALL_TIMEOUT = 120.0


def _build_stdio_params(server) -> Any:
    """根据 server 配置构造 StdioServerParameters（command/args/env）。"""
    from mcp.client.stdio import StdioServerParameters
    args = list(server.args or [])
    env = dict(os.environ)
    if server.env:
        for k, v in (server.env or {}).items():
            if v is None:
                env.pop(k, None)
            else:
                env[str(k)] = str(v)
    return StdioServerParameters(command=server.command, args=args, env=env)


async def _connect(server) -> Any:
    """连接 stdio MCP 服务器，返回 (stdio_client ctx, ClientSession)。"""
    from mcp.client.stdio import stdio_client
    from mcp import ClientSession
    params = _build_stdio_params(server)
    ctx = stdio_client(params)
    read, write = await ctx.__aenter__()
    session = ClientSession(read, write)
    await session.__aenter__()
    await session.initialize()
    return ctx, session


def _to_openai_tool(server_name: str, tool) -> dict:
    """MCP 工具 → OpenAI function 定义（name 带服务器前缀防冲突）。"""
    name = f"mcp__{server_name}__{tool.name}"
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {
        "type": "object",
        "properties": {},
    }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"[MCP:{server_name}] {tool.description or tool.name}",
            "parameters": schema,
        },
    }


async def _discover_async(server) -> list[dict]:
    """连接服务器并发现工具（async 内部实现）。"""
    ctx = None
    session = None
    try:
        ctx, session = await _connect(server)
        tools = await asyncio.wait_for(session.list_tools(), timeout=_CALL_TIMEOUT)
        return [_to_openai_tool(server.name, t) for t in (tools.tools or [])]
    finally:
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass


def discover_tools(server, force_refresh: bool = False) -> list[dict]:
    """发现服务器工具（缓存 TTL 60s）。失败抛 ValueError（含原因，供前端提示）。"""
    sid = str(server.id)
    now = time.time()
    hit = _discovery_cache.get(sid)
    if not force_refresh and hit and hit[0] > now:
        return hit[1]
    try:
        tools = asyncio.run(_discover_async(server))
    except Exception as e:
        _discovery_cache.pop(sid, None)
        raise ValueError(f"MCP 服务器「{server.name}」连接失败：{_short_err(e)}")
    _discovery_cache[sid] = (now + _DISCOVERY_TTL, tools)
    return tools


async def _call_async(server, tool_name: str, arguments: dict) -> str:
    """调用 MCP 工具（async 内部实现）。返回结果文本。"""
    ctx = None
    session = None
    try:
        ctx, session = await _connect(server)
        result = await asyncio.wait_for(
            session.call_tool(tool_name, arguments or {}), timeout=_CALL_TIMEOUT
        )
        return _format_mcp_result(result)
    finally:
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass


def call_tool(server, tool_name: str, arguments: dict) -> str:
    """调用 MCP 工具，返回结果文本。失败抛 ValueError。"""
    try:
        return asyncio.run(_call_async(server, tool_name, arguments))
    except Exception as e:
        raise ValueError(f"MCP 工具「{tool_name}」调用失败：{_short_err(e)}")


def _format_mcp_result(result) -> str:
    """MCP CallToolResult → 文本（content 各块拼接；结构化结果转 JSON）。"""
    import json
    parts: list[str] = []
    content = getattr(result, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", "")
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif block_type == "image":
            parts.append("[图片结果]")
    if not parts and getattr(result, "structuredContent", None):
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False, default=str)[:4000])
    return "\n".join(parts)[:8000] or "（无输出）"


def _short_err(e: Exception) -> str:
    """异常转简短中文原因。"""
    from app.providers.errors import map_to_chinese
    msg = str(e) or e.__class__.__name__
    return msg[:300] if "MCP 服务器" in msg else map_to_chinese(e)
