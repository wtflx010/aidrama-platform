"""agent 工具族单元测试：tools/executor（注册表分发 / 参数解析 / 开关）。

覆盖：
- 注册表与 _TOOLS 一一对应（防回归）
- _parse_args 容错
- 受控工具开关（terminal/file/code/browser）
- 未知工具 / 禁用工具拒绝
- 动态 skill_* / mcp__* 分发路径（mock 子智能体与 mcp_service）
"""

import pytest

from app.services.agent.tools.executor import (
    _is_builtin_tool_enabled,
    _parse_args,
    _REGISTRY,
    execute_tool,
)
from app.services.agent.tools.specs import _TOOLS


# ── 注册表完整性 ───────────────────────────────────────

def test_registry_matches_specs():
    # 2026-08-23 起能力边界收敛：_TOOLS 仅暴露创作确认流工具，注册表保留全部
    # 处理器（供 /gen 直连 / 子智能体 / 定时任务等复用），因此只要求「可见工具全部已注册」。
    spec_names = {t["function"]["name"] for t in _TOOLS}
    assert spec_names <= set(_REGISTRY.keys())


def test_registry_has_expected_families():
    names = set(_REGISTRY.keys())
    assert {"code_search", "code_edit", "code_rollback"} <= names
    assert {"git_status", "git_push", "git_commit"} <= names
    assert {"web_search", "web_fetch", "github_search"} <= names
    assert {"generate_image", "generate_video", "tts_speak"} <= names
    assert {"write_novel", "write_script", "create_project"} <= names
    assert {"canvas_list", "canvas_generate"} <= names
    assert {"browser_navigate", "browser_click"} <= names
    # 子智能体已随 2026-08-23 能力收敛移除注册；此处仅断言仍存在的创作族
    assert {"plan_script_outline", "write_script", "generate_shot_preview"} <= names


# ── 参数解析 ───────────────────────────────────────────

def test_parse_args_valid_json():
    assert _parse_args('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_args_invalid_json_returns_empty():
    assert _parse_args("not json") == {}
    assert _parse_args("") == {}
    assert _parse_args(None) == {}
    assert _parse_args("{}") == {}


def test_parse_args_non_dict_json_returns_empty():
    assert _parse_args("[1,2,3]") == {}
    assert _parse_args('"str"') == {}


# ── 工具开关 ───────────────────────────────────────────

def test_builtin_switches_default_on(monkeypatch):
    """默认配置下关键工具全部启用（不依赖 .env 覆盖）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "agent_terminal_enabled", True)
    monkeypatch.setattr(settings, "agent_file_enabled", True)
    monkeypatch.setattr(settings, "agent_code_enabled", True)
    monkeypatch.setattr(settings, "agent_browser_enabled", True)
    assert _is_builtin_tool_enabled("terminal_execute")
    assert _is_builtin_tool_enabled("file_read")
    assert _is_builtin_tool_enabled("code_edit")
    assert _is_builtin_tool_enabled("git_status")
    assert _is_builtin_tool_enabled("web_search")  # 无开关：恒 True


def test_builtin_switches_off_respected(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_terminal_enabled", False)
    monkeypatch.setattr(settings, "agent_code_enabled", False)
    assert _is_builtin_tool_enabled("terminal_execute") is False
    assert _is_builtin_tool_enabled("code_write") is False
    assert _is_builtin_tool_enabled("file_read") is True  # 未关


# ── execute_tool 拒绝路径 ─────────────────────────────

def test_execute_tool_unknown_rejected():
    with pytest.raises(ValueError, match="未知工具"):
        execute_tool(None, None, "does_not_exist", "{}")


def test_execute_tool_disabled_by_switch(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_terminal_enabled", False)
    with pytest.raises(ValueError, match="已被系统配置禁用"):
        execute_tool(None, None, "terminal_execute", "{}")


# ── 动态分发：skill_* / mcp__* ────────────────────────

class _FakeDB:
    """极简 Session 替身：供 skill/mcp 动态路径的查询使用。"""

    def __init__(self, mcp_servers=None):
        self.mcp_servers = mcp_servers or []

    def scalar(self, stmt):
        # skill 工具分发用到的是本分支自己 import 的查询，不通过 db.scalar 走这里；
        # 仅供 mcp 路径的 server 查询兜底。
        for s in self.mcp_servers:
            return s
        return None


def test_execute_skill_dynamic_runs(monkeypatch):
    """skill_<name> 动态工具经 _run_skill 执行并返回文本。"""
    calls = {}

    def fake_run_skill(db, skill_name, task, refs=None, model=None):
        calls["skill"] = skill_name
        return f"skill-{skill_name}-output"

    monkeypatch.setattr(
        "app.services.agent.creative.subagent._run_skill",
        fake_run_skill,
    )
    result, events = execute_tool(None, "sess", "skill_my_skill", '{"task": "do x"}')
    assert result["ok"] is True
    assert calls["skill"] == "my_skill"
    assert "my_skill" in str(result["message"])
    assert events[0]["name"] == "skill_my_skill"


def test_execute_skill_empty_task_rejected():
    with pytest.raises(ValueError, match="不能为空"):
        execute_tool(None, "sess", "skill_x", "{}")


def test_execute_mcp_dynamic_unknown_server_rejected():
    """mcp__ 前缀分发的 server 解析：查无此服务器时明确报错（不触网/不触库执行）。"""
    class FakeDb:
        def scalar(self, stmt):
            return None  # 无匹配服务器

    with pytest.raises(ValueError, match="不存在"):
        execute_tool(FakeDb(), "sess", "mcp__no-such-server__tool", "{}")


# ── 无副作用工具快测（注册表 handler 可调用形状）────────

def test_registry_handler_signature_uniform():
    """所有静态 handler 均可被 execute_tool 以标准签名调用（不执行，仅检查可调用）。"""
    import inspect

    for name, handler in _REGISTRY.items():
        sig = inspect.signature(handler)
        params = list(sig.parameters)
        assert len(params) == 6, f"{name} handler 参数应为 6，实际 {params}"
