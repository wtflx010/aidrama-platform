"""A 方案重构 · 兼容面断言（回归护栏）。

验证目标：
1. 新包 app.services.agent.* 的全部符号可导入（引擎/记忆/工具/创作/工作流）。
2. 老文件 agent_service.py 的对外符号清单（agent.py / gen.py 引用的 58 个）仍然存在。

本测试是门面化/迁移的回归基线：任一步迁移后都应通过。
运行：cd backend && .venv/bin/python -m pytest tests/test_agent_service_facade.py -q
"""

import importlib

import pytest

# ── 新包模块清单（每次迁移完成后应全部可导入）────────────
NEW_PKG_MODULES = [
    "app.services.agent.engine.chunks",
    "app.services.agent.engine.events",
    "app.services.agent.engine.models",
    "app.services.agent.engine.parsing",
    "app.services.agent.engine.constants",
    "app.services.agent.engine.context",
    "app.services.agent.engine.sessions",
    "app.services.agent.engine.stream",
    "app.services.agent.memory.tfidf",
    "app.services.agent.memory.store",
    "app.services.agent.tools.meta",
    "app.services.agent.tools.specs",
    "app.services.agent.tools.web",
    "app.services.agent.tools.shell",
    "app.services.agent.tools.code",
    "app.services.agent.tools.git",
    "app.services.agent.tools.skill",
    "app.services.agent.tools.executor",
    "app.services.agent.creative.subagent",
    "app.services.agent.creative.media",
    "app.services.agent.creative.project",
    "app.services.agent.creative.writing",
    "app.services.agent.agents.novel_api",
    "app.services.agent.agents.plans",
    "app.services.agent.agents.goals",
]

# ── 老文件对外符号（agent.py / gen.py 引用；门面必须保住）──
FACADE_SYMBOLS = [
    # 会话/消息类
    "list_sessions", "search_sessions", "create_session", "delete_session",
    "fork_session", "optimize_prompt", "compact_session", "list_messages",
    "AgentSession",
    # 引擎
    "chat_stream",
    # 项目/创作
    "confirm_project", "project_delete_confirm", "_tool_generate_image",
    "_tool_generate_video", "_tool_tts_speak", "_tool_write_novel",
    "_tool_write_script", "_build_project_draft", "_PROJECT_DRAFT_PROMPT",
    # 子智能体/模型
    "_run_subagent_core", "_load_role_configs", "_resolve_chat_model",
    "web_search_with_cache",
    # 小说
    "generate_novel_outline", "submit_novel_writing",
    # 记忆
    "list_memory_items", "create_memory_item", "delete_memory_item", "search_memories",
    # 规划
    "create_plan", "get_plan", "list_plans", "confirm_plan", "mark_plan_step_done",
    "delete_plan",
    # 目标
    "create_goal", "get_goal", "list_goals", "set_goal_status", "evaluate_goal",
    "goal_advance_message",
    # Skill
    "list_skills", "create_skill", "update_skill", "delete_skill",
    # 规则
    "list_rules", "create_rule", "update_rule", "delete_rule",
    # 插件
    "list_plugins", "create_plugin", "update_plugin", "delete_plugin",
    # MCP
    "list_mcp_servers", "create_mcp_server", "update_mcp_server", "delete_mcp_server",
    "test_mcp_server",
    # 角色
    "list_roles", "create_role", "update_role", "delete_role",
    # git
    "_git_commit_execute", "_git_push_execute",
]


@pytest.mark.parametrize("module", NEW_PKG_MODULES)
def test_new_pkg_module_imports(module: str):
    importlib.import_module(module)


@pytest.mark.parametrize("sym", FACADE_SYMBOLS)
def test_facade_symbol_exists(sym: str):
    import app.services.agent_service as facade

    assert hasattr(facade, sym), f"agent_service.{sym} 缺失（破坏 API 兼容面）"


def test_executor_registry_covers_all_specs():
    """可见工具 schema 必须全部注册（注册表可保有 /gen 直连等内部工具）。"""
    from app.services.agent.tools.executor import _REGISTRY
    from app.services.agent.tools.specs import _TOOLS

    spec_names = {t["function"]["name"] for t in _TOOLS}
    assert spec_names <= set(_REGISTRY.keys()), "executor 注册表缺少 _TOOLS 中的工具"


def test_executor_exports_legacy_names():
    """兼容旧调用名：agent_service._execute_tool 语义 == 新 executor.execute_tool。"""
    from app.services.agent.tools import executor as new_exec
    import app.services.agent_service as old

    assert callable(getattr(old, "_execute_tool", None))
    assert new_exec.execute_tool is not None
    assert new_exec._execute_tool is new_exec.execute_tool
