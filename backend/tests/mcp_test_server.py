"""最小 MCP 测试服务器（mcp 2.0 lowlevel API）：提供 echo / add 两个工具。"""
import anyio
import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.types import CallToolRequestParams, PaginatedRequestParams, ServerCapabilities

server = Server("test-echo-server")

_TOOLS = [
    types.Tool(
        name="echo",
        description="回显输入的文本",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    types.Tool(
        name="add",
        description="两个整数相加",
        inputSchema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    ),
]


async def handle_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=_TOOLS)


async def handle_call_tool(ctx, params) -> types.CallToolResult:
    name = params.name
    arguments = params.arguments or {}
    if name == "echo":
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"echo: {arguments.get('text', '')}")])
    if name == "add":
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"sum: {int(arguments.get('a', 0)) + int(arguments.get('b', 0))}")])
    return types.CallToolResult(content=[types.TextContent(type="text", text=f"unknown tool {name}")], isError=True)


server.add_request_handler("tools/list", PaginatedRequestParams, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


async def main():
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(
            read, write,
            InitializationOptions(
                server_name="test-echo-server",
                server_version="0.1.0",
                capabilities=ServerCapabilities(tools={}),
            ),
        )


if __name__ == "__main__":
    anyio.run(main)
