"""agent 工具族单元测试：tools/shell（路径安全 / 命令黑名单 / 敏感文件保护）。

A 方案重构后工具族位于 app.services.agent.*，测试纯逻辑不触网/不触库。
"""

import pytest

from app.services.agent.tools.shell import (
    _check_terminal_command,
    _ensure_not_sensitive,
    _resolve_agent_path,
    _SENSITIVE_PATTERNS,
    _TERMINAL_DENYLIST,
)


# ── 路径安全解析 ───────────────────────────────────────

def test_resolve_agent_path_relative(tmp_path, monkeypatch):
    """相对路径按 AGENT_WORKDIR 拼接，返回绝对路径。"""
    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    (tmp_path / "sub").mkdir()
    p = _resolve_agent_path("sub/file.py")
    assert p == (tmp_path / "sub" / "file.py").resolve()


def test_resolve_agent_path_absolute_inside_ok(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    f = tmp_path / "inside.txt"
    f.write_text("x")
    assert _resolve_agent_path(str(f)) == f.resolve()


def test_resolve_agent_path_traversal_rejected(tmp_path, monkeypatch):
    """越出工作目录（.. 穿越 / 绝对路径指向外部）必须拒绝。"""
    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    with pytest.raises(ValueError, match="超出允许范围"):
        _resolve_agent_path("../../etc/passwd")
    with pytest.raises(ValueError, match="超出允许范围"):
        _resolve_agent_path("/etc/passwd")


# ── 终端命令黑名单 ─────────────────────────────────────

def test_terminal_denylist_blocks_destructive():
    for bad in ("rm -rf /", "rm -fr ~", "shutdown", "mkfs.ext4", "dd of=/dev/sda"):
        with pytest.raises(ValueError, match="拦截"):
            _check_terminal_command(bad)


def test_terminal_denylist_patterns_present():
    """黑名单含全部 4 类高危模式（根目录删除/炸弹/格式化/关机）。"""
    joined = "\n".join(_TERMINAL_DENYLIST)
    for pattern in ("rm -rf /", ":(){", "mkfs", "shutdown", "dd of=/dev/"):
        assert pattern in joined or pattern in _TERMINAL_DENYLIST


def test_terminal_denylist_allows_safe():
    """正常开发命令不受影响。"""
    for ok in ("git status", "ls -la", "pip install requests", "npm run build"):
        _check_terminal_command(ok)  # 不抛错即通过


def test_terminal_denylist_case_insensitive():
    with pytest.raises(ValueError):
        _check_terminal_command("RM -RF /")


# ── 敏感文件保护 ───────────────────────────────────────

def test_sensitive_patterns_complete():
    joined = "\n".join(_SENSITIVE_PATTERNS)
    for pat in (".env", ".pem", "id_rsa", ".pgpass", "credentials.json"):
        assert pat in joined


def test_ensure_not_sensitive_blocks_env(tmp_path):
    p = tmp_path / "backend" / ".env.example"
    with pytest.raises(ValueError, match="敏感文件"):
        _ensure_not_sensitive(p)


def test_ensure_not_sensitive_blocks_key(tmp_path):
    p = tmp_path / "id_rsa"
    with pytest.raises(ValueError, match="敏感文件"):
        _ensure_not_sensitive(p)


def test_ensure_not_sensitive_allows_normal(tmp_path):
    p = tmp_path / "main.py"
    _ensure_not_sensitive(p)  # 不抛错即通过
    p2 = tmp_path / "README.md"
    _ensure_not_sensitive(p2)


# ── 文件读写的安全校验路径 ─────────────────────────────

def test_file_read_rejects_outside(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    from app.services.agent.tools.shell import _tool_file_read

    with pytest.raises(ValueError, match="超出允许范围"):
        _tool_file_read({"path": "/etc/hostname"})
