"""引擎内核层：SSE 流式对话内核（会话/模型/上下文/事件/主循环）。

子模块按需独立 import（`from app.services.agent.engine.chunks import ...`），
不在本 __init__ 聚合导入，避免迁移中间态的循环导入。
"""
