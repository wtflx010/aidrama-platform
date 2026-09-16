"""agent 工具族单元测试：tools/git（在临时 git 仓库内验证读操作）。

仅测只读命令（status/diff/log/branch）——避免测试污染真实仓库；
commit 等需确认的执行路径由 agent.py 门面覆盖（本次不跑真提交）。
"""

import subprocess

import pytest

from app.services.agent.tools.git import _run_git, _tool_git_branch, _tool_git_diff, _tool_git_log, _tool_git_status


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """初始化临时 git 仓库，AGENT_WORKDIR 指向它。"""
    import os

    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True, capture_output=True)
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=str(tmp_path), check=True, capture_output=True)
    return tmp_path


def test_git_status_clean(git_repo):
    out = _tool_git_status({})
    assert "git status" in out
    # 干净仓库：除标题与 ## 分支行外无任何修改行
    dirty_lines = [ln for ln in out.splitlines()
                   if ln.strip() and not ln.startswith("git status") and not ln.strip().startswith("##")]
    assert dirty_lines == []


def test_git_status_dirty(git_repo):
    (git_repo / "f.txt").write_text("modified", encoding="utf-8")
    out = _tool_git_status({})
    assert "f.txt" in out


def test_git_log_has_commit(git_repo):
    out = _tool_git_log({})
    assert "initial" in out
    assert "git log" in out


def test_git_branch_main(git_repo):
    out = _tool_git_branch({})
    assert "git branch" in out
    assert "main" in out or "master" in out


def test_git_diff_after_change(git_repo):
    (git_repo / "f.txt").write_text("changed!!", encoding="utf-8")
    out = _tool_git_diff({})
    assert "f.txt" in out
    assert "changed" in out


def test_git_diff_clean(git_repo):
    out = _tool_git_diff({})
    assert "没有未提交" in out


def test_run_git_captures_output(git_repo):
    proc = _run_git(["status", "--short"])
    assert proc.returncode == 0


def test_non_repo_returns_helpful_message(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_workdir", str(tmp_path))
    out = _tool_git_status({})
    assert "不是 Git 仓库" in out
