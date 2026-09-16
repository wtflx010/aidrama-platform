"""agent 工具族单元测试：tools/meta（角色/规则/插件/MCP 配置加载与 CRUD 校验）。

用极简 FakeDB 模拟 SQLAlchemy Session 的 scalar/scalars/get 行为，
不依赖真实数据库。
"""

import pytest

from app.services.agent.tools.meta import (
    _AGENT_ROLES,
    _BUILTIN_PLUGINS,
    _PLUGIN_MODES,
    _ensure_builtin_plugins,
    _load_enabled_rules,
    _load_role_configs,
    _validate_mcp_command,
    create_plugin,
    create_rule,
)


# ── 内置角色兜底 ───────────────────────────────────────

def test_builtin_roles_present():
    assert {"screenwriter", "director", "artist"} <= set(_AGENT_ROLES.keys())
    for role in _AGENT_ROLES.values():
        assert role["name"]
        assert role["prompt"]


def test_load_role_configs_falls_back_to_builtin():
    """DB 无自定义角色时，返回内置三角色兜底。"""
    class FakeResult:
        def all(self):
            return []

    class FakeDB:
        def scalars(self, stmt):
            return FakeResult()

    roles = _load_role_configs(FakeDB())
    assert roles["screenwriter"]["name"] == "编剧"
    assert "kind" in roles["screenwriter"]


def test_load_role_configs_db_overrides(monkeypatch):
    """DB 自定义角色同名覆盖内置。"""
    class FakeRole:
        role_key = "screenwriter"
        name = "自定义编剧"
        persona = "我是自定义的"
        tools = ["web_search"]
        kind = "chat"

    class FakeResult:
        def all(self):
            return [FakeRole()]

    class FakeDB:
        def scalars(self, stmt):
            return FakeResult()

    roles = _load_role_configs(FakeDB())
    assert roles["screenwriter"]["name"] == "自定义编剧"
    assert roles["screenwriter"]["prompt"] == "我是自定义的"


# ── 规则加载 ───────────────────────────────────────────

def test_load_enabled_rules_formats():
    class FakeRule:
        def __init__(self, name, content):
            self.name = name
            self.content = content
            self.enabled = True
            self.scope = "global"
            self.sort = 1

    class FakeResult:
        def all(self):
            return [FakeRule("风格规则", "古风题材必须写实")]

    class FakeDB:
        def scalars(self, stmt):
            return FakeResult()

    rules = _load_enabled_rules(FakeDB())
    assert rules == ["- [风格规则] 古风题材必须写实"]


def test_create_rule_validation():
    """参数校验：空名称/内容、非法 scope、项目级缺 project_id 均拒绝。"""
    class Payload:
        def __init__(self, **kw):
            self.name = kw.get("name", "")
            self.content = kw.get("content", "")
            self.scope = kw.get("scope", "global")
            self.project_id = kw.get("project_id", None)
            self.enabled = True
            self.sort = 1

    class FakeDB:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            pass

        def refresh(self, obj):
            pass

        def scalar(self, stmt):
            return None

    db = FakeDB()
    with pytest.raises(ValueError, match="名称与内容"):
        create_rule(db, Payload())
    with pytest.raises(ValueError, match="scope"):
        create_rule(db, Payload(name="x", content="y", scope="bad"))
    with pytest.raises(ValueError, match="项目级规则必须"):
        create_rule(db, Payload(name="x", content="y", scope="project"))
    # 合法 → 入库
    rule = create_rule(db, Payload(name="ok", content="内容", scope="global"))
    assert rule is not None


# ── MCP 命令校验 ───────────────────────────────────────

def test_validate_mcp_command_accepts_launchers():
    assert _validate_mcp_command("npx") == "npx"
    assert _validate_mcp_command("uvx") == "uvx"
    assert _validate_mcp_command("python3.11") == "python3.11"


def test_validate_mcp_command_rejects_paths_and_shell():
    for bad in ("/bin/rm", "npx foo", "npx;rm -rf /", "npx$(id)", "../evil"):
        with pytest.raises(ValueError, match="command"):
            _validate_mcp_command(bad)


# ── 插件模式白名单 ─────────────────────────────────────

def test_plugin_modes_whitelist():
    assert _PLUGIN_MODES == ("t2i", "i2i", "t2v", "i2v", "multi_ref")


def test_builtin_plugins_config_complete():
    for p in _BUILTIN_PLUGINS:
        assert p["name"] in _PLUGIN_MODES
        assert p["label"]
        assert p["prompt"]
        assert p["tool"] in ("generate_image", "generate_video")
        assert p["mode"] in _PLUGIN_MODES


def test_create_plugin_mode_validation():
    class Payload:
        name = "custom"
        label = "自定义"
        description = "d"
        prompt = "p"
        tool = "generate_image"
        mode = "bogus"
        enabled = True
        sort = 1

    class FakeDB:
        def scalar(self, stmt):
            return None

        def add(self, obj):
            pass

        def commit(self):
            pass

        def refresh(self, obj):
            pass

    with pytest.raises(ValueError, match="执行模式"):
        create_plugin(FakeDB(), Payload())


# ── 内置插件种子 ───────────────────────────────────────

def test_ensure_builtin_plugins_seeds(monkeypatch):
    """首次运行（空库）种入 5 个内置插件。"""
    seeded = []

    class FakePlugin:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeDB:
        def scalar(self, stmt):
            return None  # 无既有行 → 全部创建

        def add(self, obj):
            seeded.append(obj)

        def commit(self):
            pass

    _ensure_builtin_plugins(FakeDB())
    assert len(seeded) == 5
    assert {s.name for s in seeded} == set(_PLUGIN_MODES)
