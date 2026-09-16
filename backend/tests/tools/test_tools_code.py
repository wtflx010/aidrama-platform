"""agent 工具族单元测试：tools/code（检查点快照 / 读 / 写 / 编辑 / 回滚）。

在 tmp_path 上模拟 AGENT_WORKDIR，验证写前快照与回滚行为（纯文件系统，无网络/DB）。
"""

import pytest

from app.services.agent.tools.code import (
    _checkpoint_snapshot,
    _checkpoints_dir,
    _tool_code_edit,
    _tool_code_list,
    _tool_code_read,
    _tool_code_rollback,
    _tool_code_write,
)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """把 AGENT_WORKDIR 指向临时目录，并清空既有检查点。"""
    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    return tmp_path


def test_code_write_creates_file(workdir):
    out = _tool_code_write({"path": "hello.txt", "content": "你好，测试"})
    assert "已写入" in out
    assert (workdir / "hello.txt").read_text(encoding="utf-8") == "你好，测试"


def test_code_read_with_line_numbers(workdir):
    (workdir / "a.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
    out = _tool_code_read({"path": "a.py"})
    assert "共 3 行" in out
    assert "1 line1" in out
    assert "3 line3" in out


def test_code_read_range(workdir):
    (workdir / "b.py").write_text("".join(f"L{i}\n" for i in range(1, 6)), encoding="utf-8")
    out = _tool_code_read({"path": "b.py", "start": 2, "end": 4})
    assert "L2" in out and "L4" in out
    assert "L1" not in out


def test_code_write_creates_checkpoint(workdir):
    (workdir / "v1.txt").write_text("old", encoding="utf-8")
    _tool_code_write({"path": "v1.txt", "content": "new"})
    assert (workdir / "v1.txt").read_text(encoding="utf-8") == "new"
    # 写前已存档 → 存在快照
    cdir = _checkpoints_dir()
    assert cdir.is_dir()
    snaps = list(cdir.iterdir())
    assert len(snaps) >= 1
    # 快照内容为旧值
    old_files = [s for s in snaps[0].rglob("*") if s.is_file()]
    assert any(f.read_text(encoding="utf-8") == "old" for f in old_files)


def test_code_edit_unique_replace(workdir):
    (workdir / "c.txt").write_text("foo bar foo", encoding="utf-8")
    out = _tool_code_edit({"path": "c.txt", "old": "bar", "new": "BAZ"})
    assert "已修改" in out
    assert (workdir / "c.txt").read_text(encoding="utf-8") == "foo BAZ foo"


def test_code_edit_ambiguous_rejected(workdir):
    (workdir / "d.txt").write_text("dup dup", encoding="utf-8")
    with pytest.raises(ValueError, match="出现 2 次"):
        _tool_code_edit({"path": "d.txt", "old": "dup", "new": "x"})


def test_code_edit_not_found_rejected(workdir):
    (workdir / "e.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="未找到"):
        _tool_code_edit({"path": "e.txt", "old": "nope", "new": "x"})


def test_code_rollback_restores(workdir):
    (workdir / "r.txt").write_text("original", encoding="utf-8")
    _tool_code_write({"path": "r.txt", "content": "changed v1"})
    _tool_code_write({"path": "r.txt", "content": "changed v2"})
    # 列出快照（不带参数）
    listing = _tool_code_rollback({"path": ""})
    assert "快照" in listing or "检查点" in listing
    # 恢复最近快照（不带 snapshot → 回滚指定文件的最近一个）
    out = _tool_code_rollback({"path": "r.txt"})
    assert "已恢复" in out
    # 最近快照是 "changed v1"（第二次写前的存档）
    assert (workdir / "r.txt").read_text(encoding="utf-8") == "changed v1"


def test_code_rollback_no_checkpoints(workdir):
    out = _tool_code_rollback({"path": ""})
    assert "暂无" in out or "检查点" in out


def test_code_list_pattern(workdir):
    (workdir / "x.ts").write_text("", encoding="utf-8")
    (workdir / "y.js").write_text("", encoding="utf-8")
    out = _tool_code_list({"pattern": "*.ts"})
    assert "x.ts" in out
    assert "y.js" not in out


def test_checkpoint_snapshot_nonexistent_returns_none(workdir):
    assert _checkpoint_snapshot(workdir / "no-such-file.txt") is None


def test_checkpoint_snapshot_returns_marker(workdir):
    (workdir / "s.txt").write_text("data", encoding="utf-8")
    marker = _checkpoint_snapshot(workdir / "s.txt")
    assert marker is not None
    assert "::" in marker
