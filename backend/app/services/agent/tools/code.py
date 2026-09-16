"""工具注册层 · 编码工具族（P8，对齐 TraeWork Code 模式）。

从 agent_service.py 剥离（原行号 4632~5120 区域 + _TOOL_CODE_DISPATCH），
逻辑未改动：code_search / code_list / code_read / code_edit / code_write /
code_execute / code_diagnose / code_rollback + 检查点快照。
"""

from pathlib import Path


def _checkpoints_dir() -> Path:
    """检查点快照根目录（项目根/.agent_checkpoints，不入 git）。"""
    from app.config import settings

    return Path(settings.agent_workdir) / ".agent_checkpoints"


def _checkpoint_snapshot(path: Path) -> str | None:
    """写前快照：把文件原内容备份到快照目录，返回快照标识（原文件不存在返回 None）。"""
    import shutil
    import time

    if not path.exists():
        return None
    from app.config import settings

    root = Path(settings.agent_workdir)
    ts = time.strftime("%Y%m%d-%H%M%S")
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    snap = _checkpoints_dir() / ts / rel
    snap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, snap)
    # 快照轮转：仅保留最近 50 个
    snaps = sorted(_checkpoints_dir().iterdir()) if _checkpoints_dir().exists() else []
    for old in snaps[:-50]:
        import shutil as _sh

        _sh.rmtree(old, ignore_errors=True)
    return f"{ts}::{rel}"


def _iter_project_files(start: Path, root: Path) -> list[Path]:
    """遍历项目文件（跳过 venv/node_modules/dist 等大目录），保持与路径安全一致。"""
    import os

    SKIP_DIRS = {
        ".git", ".venv", "venv", "node_modules", ".agent_checkpoints",
        ".pytest_cache", "__pycache__", ".next", ".trae", ".local", "dist",
        "build", ".idea", ".vscode",
    }
    if start.is_file():
        return [start]
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                p.relative_to(root)
            except ValueError:
                continue
            out.append(p)
    return out


def _resolve_path(path: str) -> Path:
    """路径安全解析（转发自 tools.shell 的 _resolve_agent_path，避免包内循环）。"""
    from app.services.agent.tools.shell import _resolve_agent_path
    return _resolve_agent_path(path)


def _ensure_not_sensitive(p: Path) -> None:
    """敏感文件保护（转发自 tools.shell）。"""
    from app.services.agent.tools.shell import _ensure_not_sensitive
    return _ensure_not_sensitive(p)


def _tool_code_search(args: dict) -> str:
    """正则搜索代码库（对标 Grep）：返回「文件:行号:内容」命中列表，上限 50 条。"""
    import re

    pattern = (args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern 不能为空")
    glob_ = (args.get("glob") or "").strip()
    case = bool(args.get("case_sensitive"))
    from app.config import settings

    root = Path(settings.agent_workdir)
    raw_path = (args.get("path") or "").strip()
    start = _resolve_path(raw_path) if raw_path else root
    if not start.exists():
        raise ValueError(f"路径不存在：{start}")
    try:
        rx = re.compile(pattern, 0 if case else re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"正则表达式无效：{e}")
    from fnmatch import fnmatch

    files = _iter_project_files(start, root)
    if glob_:
        files = [f for f in files if fnmatch(f.name, glob_)]
    hits: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not rx.search(line):
                continue
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            hits.append(f"{rel}:{i}: {line.strip()[:200]}")
            if len(hits) >= 50:
                break
        if len(hits) >= 50:
            break
    if not hits:
        return "未找到匹配内容"
    return f"共 {len(hits)} 处匹配（最多展示 50）：\n" + "\n".join(hits)


def _tool_code_list(args: dict) -> str:
    """按 glob 模式列出文件/目录（对标 Glob/LS），返回相对路径列表。"""
    pattern = (args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern 不能为空")
    from app.config import settings

    root = Path(settings.agent_workdir)
    raw_path = (args.get("path") or "").strip()
    start = _resolve_path(raw_path) if raw_path else root
    if not start.exists():
        raise ValueError(f"路径不存在：{start}")
    matches: list[Path] = []
    for p in sorted(start.glob(pattern)):
        try:
            p.relative_to(root)
        except ValueError:
            continue
        matches.append(p)
    if not matches:
        return f"未匹配到任何文件（pattern={pattern}，目录={start}）"
    lines = [str(m.relative_to(root)) + ("/" if m.is_dir() else "") for m in matches[:200]]
    if len(matches) > 200:
        lines.append(f"…（共 {len(matches)} 项，仅展示前 200）")
    return "\n".join(lines)


def _tool_code_read(args: dict) -> str:
    """读取文件（带行号，支持行范围，超长截断）。"""
    path = (args.get("path") or "").strip()
    if not path:
        raise ValueError("path 不能为空")
    p = _resolve_path(path)
    _ensure_not_sensitive(p)
    if not p.exists() or not p.is_file():
        raise ValueError(f"文件不存在：{p}")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return f"（二进制文件，{p.stat().st_size} 字节，无法作为文本读取）"
    except OSError as e:
        raise ValueError(f"读取失败：{e}")
    total = len(lines)
    start = max(1, int(args.get("start") or 1))
    end = min(total, int(args.get("end") or total))
    if start > total:
        return f"{p}：文件共 {total} 行，起始行 {start} 超出范围"
    seg = lines[start - 1 : end]
    width = len(str(end))
    body = "\n".join(f"{start + i:>{width}} {ln}" for i, ln in enumerate(seg))
    if len(seg) > 120:
        body = "\n".join(f"{start + i:>{width}} {ln}" for i, ln in enumerate(seg[:120]))
        body += f"\n…（共 {len(seg)} 行，仅展示前 120 行，可用 start/end 分段读取）"
    return f"{p}（共 {total} 行）\n{body}"


def _tool_code_edit(args: dict) -> str:
    """精确替换文件内容（对标 SearchReplace）：old 唯一匹配，写前自动检查点快照。"""
    path = (args.get("path") or "").strip()
    old = args.get("old") or ""
    new = args.get("new") or ""
    replace_all = bool(args.get("replace_all"))
    if not path or not old:
        raise ValueError("path / old 不能为空")
    p = _resolve_path(path)
    _ensure_not_sensitive(p)
    if not p.exists() or not p.is_file():
        raise ValueError(f"文件不存在：{p}")
    try:
        content = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        raise ValueError(f"无法读取文件进行编辑：{e}")
    count = content.count(old)
    if count == 0:
        raise ValueError(
            "未找到要替换的原文（old 与文件内容不一致）。请先用 code_read 查看文件实际内容，"
            "再提供精确匹配的 old 文本"
        )
    if count > 1 and not replace_all:
        raise ValueError(f"old 在文件中出现 {count} 次，请带更多上下文使其唯一，或设 replace_all=true")
    _checkpoint_snapshot(p)
    content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
    p.write_text(content, encoding="utf-8")
    return f"已修改 {p}（替换 {count if replace_all else 1} 处，原文件已存档可 code_rollback 撤销）"


def _tool_code_write(args: dict) -> str:
    """创建/覆写文件，写前自动检查点快照。"""
    path = (args.get("path") or "").strip()
    content = args.get("content") or ""
    if not path:
        raise ValueError("path 不能为空")
    p = _resolve_path(path)
    _ensure_not_sensitive(p)
    if p.exists():
        _checkpoint_snapshot(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p}（{len(content)} 字，原文件已存档可 code_rollback 撤销）"


def _tool_code_execute(args: dict) -> str:
    """解释执行 Python / JavaScript 代码：超时 30s、输出上限 64KB。

注意：并非隔离沙箱——代码拥有与后端进程相同的文件/网络权限，
仅在「本机绑定 + API 鉴权 + 高危命令黑名单」防护下可用，切勿对公网开放。"""
    code = (args.get("code") or "").strip()
    language = (args.get("language") or "python").strip().lower()
    if not code:
        raise ValueError("code 不能为空")
    import os
    import subprocess

    from app.config import settings

    if language == "python":
        cmd = ["python3", "-"]
    elif language in ("javascript", "js", "node"):
        cmd = ["node", "-"]
    else:
        raise ValueError("language 仅支持 python / javascript")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            cmd, input=code, capture_output=True, text=True,
            cwd=settings.agent_workdir, timeout=30, env=env,
        )
    except FileNotFoundError:
        raise ValueError(f"未找到运行环境：{cmd[0]} 不在 PATH 中（code_execute 沙箱依赖 python3/node）")
    except subprocess.TimeoutExpired:
        return "代码执行超时（>30s，已终止）。避免死循环/长驻任务，或拆小执行。"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if len(out) > 64000:
        out = out[:64000] + "\n…（输出已截断）"
    if len(err) > 64000:
        err = err[:64000] + "\n…（输出已截断）"
    parts = [out]
    if err:
        parts.append("[stderr]\n" + err)
    text = "\n".join(x for x in parts if x) or "（无输出）"
    return f"退出码 {proc.returncode}\n{text}"[:7000]


def _tool_code_diagnose(args: dict) -> str:
    """静态诊断（对标 IDE 报错）：Python 语法、JSON 合法性、JS 语法。返回「文件:行:列: 消息」。"""
    import json as _json
    import subprocess
    import sys

    path = (args.get("path") or "").strip()
    if not path:
        raise ValueError("path 不能为空")
    p = _resolve_path(path)
    _ensure_not_sensitive(p)
    if not p.exists():
        raise ValueError(f"路径不存在：{p}")
    from app.config import settings

    root = Path(settings.agent_workdir)
    files = [p] if p.is_file() else _iter_project_files(p, root)
    results: list[str] = []

    def _rel(f: Path) -> str:
        try:
            return str(f.relative_to(root))
        except ValueError:
            return str(f)

    # Python 语法检查（py_compile，快且可靠）
    py_files = [f for f in files if f.suffix == ".py"][:30]
    for f in py_files:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(f)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            results.append(f"{_rel(f)}: {r.stderr.strip()[:400]}")
    # JSON 校验
    for f in [f for f in files if f.suffix == ".json"][:20]:
        try:
            _json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            results.append(f"{_rel(f)}: JSON 解析失败：{e}")
    # JS 语法（node --check）
    js_files = [f for f in files if f.suffix in (".js", ".mjs", ".cjs")][:20]
    for f in js_files:
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            results.append(f"{_rel(f)}: {(r.stderr or r.stdout).strip()[:400]}")
    ts_count = len([f for f in files if f.suffix in (".ts", ".tsx")])
    if ts_count:
        results.append(f"（TypeScript {ts_count} 个文件：完整 tsc 编译较重，本期未启用，建议用终端跑 npx tsc --noEmit）")
    if not results:
        return f"诊断通过：{path} 下 {len(files)} 个文件未发现语法/格式错误"
    return f"诊断发现 {len(results)} 个问题：\n" + "\n".join(results)


def _tool_code_rollback(args: dict) -> str:
    """检查点回滚：列出快照或恢复指定快照（对标 Hermes filesystem checkpoints）。"""
    import shutil

    snapshot = (args.get("snapshot") or "").strip()
    path = (args.get("path") or "").strip()
    from app.config import settings

    root = Path(settings.agent_workdir)
    cdir = _checkpoints_dir()
    if not cdir.is_dir():
        return "暂无检查点快照（code_write / code_edit 写操作会自动存档）"
    if not snapshot:
        if path:
            # 仅回滚指定文件的最近快照
            target = _resolve_path(path)
            rel = target.relative_to(root)
            for s in sorted(cdir.iterdir(), reverse=True):
                if (s / rel).is_file():
                    snapshot = f"{s.name}::{rel}"
                    break
            if not snapshot:
                return f"快照中未找到文件：{path}"
        else:
            snaps = sorted(cdir.iterdir(), reverse=True)[:20]
            if not snaps:
                return "暂无检查点快照"
            lines = [f"最近 {len(snaps)} 个快照（恢复请传 snapshot）："]
            for s in snaps:
                files = [str(x.relative_to(s)) for x in s.rglob("*") if x.is_file()]
                lines.append(f"- {s.name} → {', '.join(files[:4])}{'…' if len(files) > 4 else ''}")
            return "\n".join(lines)
    snap_dir = cdir / snapshot.split("::")[0]
    if not snap_dir.is_dir():
        raise ValueError(f"快照不存在：{snapshot}（先不带参数调用 code_rollback 查看可用快照）")
    if path:
        target = _resolve_path(path)
        srcs = [x for x in snap_dir.rglob("*") if x.is_file() and x.name == target.name]
        if not srcs:
            # 按相对路径精确匹配
            rel = target.relative_to(root)
            srcs = [snap_dir / rel] if (snap_dir / rel).is_file() else []
        if not srcs:
            raise ValueError(f"快照中未找到文件：{path}")
    else:
        srcs = [x for x in snap_dir.rglob("*") if x.is_file()]
    restored: list[str] = []
    for src in srcs:
        rel = src.relative_to(snap_dir)
        dst = root / rel
        if not (dst == root or root in dst.parents):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(str(rel))
    if not restored:
        return "没有可恢复的文件"
    return f"已恢复 {len(restored)} 个文件（snapshot={snap_dir.name}）：\n" + "\n".join(restored)


_TOOL_CODE_DISPATCH = {
    "code_search": _tool_code_search,
    "code_list": _tool_code_list,
    "code_read": _tool_code_read,
    "code_edit": _tool_code_edit,
    "code_write": _tool_code_write,
    "code_execute": _tool_code_execute,
    "code_diagnose": _tool_code_diagnose,
    "code_rollback": _tool_code_rollback,
}
