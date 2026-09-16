"""工具注册层 · 本地执行：终端命令 / 文件读写（含路径安全与敏感文件保护）。

从 agent_service.py 剥离（原行号 4508~4631 区域），逻辑未改动。
"""

import subprocess
from pathlib import Path


def _resolve_agent_path(path: str) -> Path:
    """路径安全解析：相对路径按 AGENT_WORKDIR（默认项目根）拼接，越出允许范围即拒绝。"""
    from app.config import settings

    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(settings.agent_workdir) / p
    root = Path(settings.agent_workdir).resolve()
    resolved = p.resolve()
    if not (resolved == root or root in resolved.parents):
        raise ValueError(f"路径超出允许范围（{root}）：{path}")
    return resolved


# ─── 终端命令安全黑名单（2026-08-14 安全加固）──────────────
# 终端工具是真实 shell（shell=True），无法做目录沙箱，只能按命令模式拦截
# 对宿主机明确破坏性/不可逆的操作；正常开发命令（git/npm/pip/ls 等）不受影响。
_TERMINAL_DENYLIST = (
    "rm -rf /", "rm -fr /", "rm -rf ~", "rm -fr ~",
    "rm -rf $home", "rm -fr $home", ":(){", "mkfs",
    "dd of=/dev/", "> /dev/sd", "shutdown", "reboot",
    "halt", "poweroff", "init 0", "init 6",
)


def _check_terminal_command(command: str) -> None:
    low = command.lower()
    for pat in _TERMINAL_DENYLIST:
        if pat in low:
            raise ValueError(
                f"命令被安全策略拦截（命中高危模式「{pat}」）："
                "终端工具不允许对宿主机执行破坏性/不可逆操作，请手动在服务器上执行。"
            )


# ─── 敏感文件保护（2026-08-14 安全加固）─────────────────
# 防止 Agent 文件工具读取/写入密钥类文件（.env、私钥、证书等），
# 避免机密经对话文本/联网工具外带。允许范围仍是 AGENT_WORKDIR，
# 仅按文件名排除敏感项。
_SENSITIVE_PATTERNS = (
    ".env", ".pem", ".key", ".p12", ".pfx",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".netrc", ".pgpass", ".npmrc", ".dockercfg",
    "credentials.json", "client_secret", "service_account",
)


def _ensure_not_sensitive(p: Path) -> None:
    name = p.name.lower()
    for pat in _SENSITIVE_PATTERNS:
        if pat.startswith("*"):
            if name.endswith(pat[1:]):
                raise ValueError(f"路径为敏感文件（{pat}），已拒绝访问：{p}")
        elif pat in name:
            raise ValueError(f"路径为敏感文件（含「{pat}」），已拒绝访问：{p}")


def _tool_terminal_execute(args: dict) -> str:
    """执行本地终端命令：shell 语法、60s 超时、非交互，返回退出码与输出。"""
    from app.config import settings

    command = (args.get("command") or "").strip()
    if not command:
        raise ValueError("命令不能为空")
    _check_terminal_command(command)
    raw_cwd = (args.get("cwd") or "").strip()
    cwd_path = _resolve_agent_path(raw_cwd) if raw_cwd else Path(settings.agent_workdir)
    timeout = settings.agent_terminal_timeout
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd_path),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (
            f"命令执行超时（>{timeout}s，已终止）：{command}\n"
            "提示：避免交互式/长驻命令（如 vim、top、watch、npm run dev），或拆成更短步骤执行。"
        )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [out]
    if err:
        parts.append("[stderr]\n" + err)
    text = "\n".join(x for x in parts if x) or "（无输出）"
    return f"退出码 {proc.returncode}，工作目录 {cwd_path}\n命令：{command}\n{text}"[:8000]


def _tool_file_read(args: dict) -> str:
    """读取文本文件（UTF-8），超长截断；二进制文件返回大小提示。"""
    path = (args.get("path") or "").strip()
    if not path:
        raise ValueError("path 不能为空")
    p = _resolve_agent_path(path)
    _ensure_not_sensitive(p)
    if not p.exists() or not p.is_file():
        raise ValueError(f"文件不存在：{p}")
    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"（二进制文件，{p.stat().st_size} 字节，无法作为文本读取）"
    except OSError as e:
        raise ValueError(f"读取失败：{e}")
    if len(content) > 8000:
        content = content[:8000] + f"\n…（文件共 {len(content)} 字，已截断，只展示前 8000 字）"
    return f"{p}：\n{content}"


def _tool_file_write(args: dict) -> str:
    """写入/创建文本文件（UTF-8），目录自动创建，已存在则覆盖。"""
    path = (args.get("path") or "").strip()
    content = args.get("content") or ""
    if not path:
        raise ValueError("path 不能为空")
    p = _resolve_agent_path(path)
    _ensure_not_sensitive(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p}（{len(content)} 字）"
