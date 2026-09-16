"""工具注册层 · Git 工具族：status / diff / log / branch + commit / push 执行。

从 agent_service.py 剥离（原行号 5102~5175 区域），逻辑未改动。
"""

import subprocess
from pathlib import Path


def _run_git(args_list: list[str], timeout: int = 30) -> "subprocess.CompletedProcess":
    """在项目根执行 git 命令，返回 CompletedProcess。"""
    from app.config import settings

    return subprocess.run(
        ["git", *args_list], capture_output=True, text=True,
        cwd=settings.agent_workdir, timeout=timeout,
    )


def _tool_git_status(args: dict) -> str:
    """查看 git 工作区状态（对标 git status --short --branch）。"""
    from app.config import settings

    root = Path(settings.agent_workdir)
    if not (root / ".git").exists():
        return "项目根不是 Git 仓库（未找到 .git 目录）"
    try:
        proc = _run_git(["status", "--short", "--branch"])
    except subprocess.TimeoutExpired:
        return "git status 执行超时"
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        out = out or ""
        out += "\n" + (proc.stderr or "").strip()
    return f"git status：\n{out or '（工作区干净）'}"[:8000]


def _tool_git_diff(args: dict) -> str:
    """查看未提交改动 diff（git diff --stat + 详情）。"""
    from app.config import settings

    root = Path(settings.agent_workdir)
    if not (root / ".git").exists():
        return "项目根不是 Git 仓库（未找到 .git 目录）"
    try:
        stat = _run_git(["diff", "--stat"])
        detail = _run_git(["diff"])
        staged_stat = _run_git(["diff", "--cached", "--stat"])
    except subprocess.TimeoutExpired:
        return "git diff 执行超时"
    parts = []
    if (stat.stdout or "").strip():
        parts.append(stat.stdout.strip())
    if (staged_stat.stdout or "").strip():
        parts.append("[已暂存]\n" + staged_stat.stdout.strip())
    if (detail.stdout or "").strip():
        parts.append("[未暂存 diff]\n" + detail.stdout.strip())
    if not parts:
        return "工作区没有未提交的改动"
    return "\n\n".join(parts)[:8000]


def _tool_git_log(args: dict) -> str:
    """查看提交历史（git log --oneline --decorate，可选按文件过滤）。"""
    from app.config import settings

    root = Path(settings.agent_workdir)
    if not (root / ".git").exists():
        return "项目根不是 Git 仓库（未找到 .git 目录）"
    limit = min(int(args.get("limit") or 20), 50)
    file = (args.get("file") or "").strip()
    cmd = ["log", "--oneline", "--decorate", "-n", str(limit), "--pretty=format:%h %ad %an %s", "--date=format:%m-%d %H:%M"]
    if file:
        from app.services.agent.tools.shell import _resolve_agent_path
        _resolve_agent_path(file)  # 越界会抛 ValueError
        cmd += ["--", file]
    try:
        proc = _run_git(cmd)
    except subprocess.TimeoutExpired:
        return "git log 执行超时"
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        out = out or ""
        out += "\n" + (proc.stderr or "").strip()
    return f"git log（最近 {limit} 条{('，文件 ' + file) if file else ''}）：\n{out or '（暂无提交）'}"[:8000]


def _tool_git_branch(args: dict) -> str:
    """查看分支列表（git branch -a -vv + 当前分支）。"""
    from app.config import settings

    root = Path(settings.agent_workdir)
    if not (root / ".git").exists():
        return "项目根不是 Git 仓库（未找到 .git 目录）"
    try:
        current = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        proc = _run_git(["branch", "-a", "-vv"])
    except subprocess.TimeoutExpired:
        return "git branch 执行超时"
    lines = []
    if current.returncode == 0 and current.stdout.strip():
        lines.append(f"当前分支：{current.stdout.strip()}")
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        out = out or ""
        out += "\n" + (proc.stderr or "").strip()
    lines.append(out or "（暂无分支）")
    return "git branch：\n" + "\n".join(lines)[:8000]


def _git_push_execute(remote: str, branch: str) -> str:
    """真正执行 git push（前端确认后调用）。"""
    from app.config import settings

    root = Path(settings.agent_workdir)
    if not (root / ".git").exists():
        raise ValueError("项目根不是 Git 仓库（未找到 .git 目录）")
    remote = (remote or "origin").strip() or "origin"
    # 安全：remote 必须是仓库已配置的远程（git remote 列表之一），
    # 禁止客户端指定任意外部 URL，否则会把仓库内容 push 到外部服务器（数据外泄）
    remotes_proc = _run_git(["remote"])
    configured = {r for r in (remotes_proc.stdout or "").splitlines() if r.strip()}
    if not configured:
        raise ValueError("仓库未配置任何远程（git remote 为空），无法推送")
    if remote not in configured:
        raise ValueError(f"远程「{remote}」未在仓库中配置（可用：{'、'.join(sorted(configured))}）")
    branch = (branch or "").strip()
    # 安全：branch 必须是合法引用名（git check-ref-format --branch），防止 git 选项注入
    if branch:
        check = _run_git(["check-ref-format", "--branch", branch])
        if check.returncode != 0:
            raise ValueError(f"分支名「{branch}」非法")
    if not branch:
        try:
            cur = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            branch = (cur.stdout or "").strip()
        except subprocess.TimeoutExpired:
            raise ValueError("无法确定当前分支")
    if not branch:
        raise ValueError("无法确定要推送的分支")
    try:
        proc = _run_git(["push", remote, branch])
    except subprocess.TimeoutExpired:
        return "git push 执行超时"
    if proc.returncode != 0:
        return f"git push 失败：{(proc.stderr or '').strip()[:800]}"
    return (proc.stdout or "").strip() or f"已推送到 {remote}/{branch}"


def _git_commit_execute(files: list[str], message: str) -> str:
    """真正执行 git 提交（前端确认后调用）：add 指定文件 → commit。"""
    from app.config import settings

    root = Path(settings.agent_workdir)
    if not (root / ".git").exists():
        raise ValueError("项目根不是 Git 仓库（未找到 .git 目录）")
    if not message.strip():
        raise ValueError("提交信息 message 不能为空")
    # 校验文件路径在项目根内（防越界）
    from app.services.agent.tools.shell import _resolve_agent_path

    safe_files: list[str] = []
    for f in files:
        _resolve_agent_path(f)  # 越界会抛 ValueError
        safe_files.append(f)
    add_args = ["add", "--"] + safe_files if safe_files else ["add", "-u"]
    try:
        add = _run_git(add_args)
        if add.returncode != 0:
            return f"git add 失败：{(add.stderr or '').strip()[:800]}"
        commit = _run_git(["commit", "-m", message.strip()])
        if commit.returncode != 0:
            return f"git commit 失败：{(commit.stderr or '').strip()[:800]}"
    except subprocess.TimeoutExpired:
        return "git 操作执行超时"
    return (commit.stdout or "").strip() or "提交成功"
