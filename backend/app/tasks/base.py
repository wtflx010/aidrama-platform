"""Celery 任务通用工具：状态回写、轮询、远程资产下载到本地。"""
import base64
import logging
import os
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)
from app.models.task import Task, TaskStatus
from app.providers.base import ProviderStatus
from app.providers.errors import ProviderError


def now():
    return datetime.now(timezone.utc)


class TaskCancelledError(ProviderError):
    """任务已被用户取消/项目已删除（2026-08-09 新增）。

    与普通 ProviderError 区分：任务 except 分支捕获本异常时**不得**把任务
    回写为 failed（取消语义：保持 cancelled，媒体状态由 task_service 已回退
    pending）。此前所有任务在 except 里统一 update_task(failed)，导致用户
    点取消后任务被覆盖成「失败」，失败数虚增。
    """


def update_task(db, task_id, **fields):
    t = db.get(Task, task_id)
    if not t:
        return
    new_status = fields.get("status")
    # 取消保护：任务被用户取消后（其他会话/进程写入 cancelled），任何状态回写
    # （running/succeeded/failed）都不得覆盖 cancelled。此前同步长任务（BGM/SFX/配音/
    # 小说/导出等）在 except 里无条件 update_task(failed)，会把 cancelled 覆盖成 failed，
    # 且终态前未二次校验。此处统一拦截：worker 的 Session 可能缓存了旧 running 状态，
    # 需 refresh 拿到跨会话最新的 cancelled 再做判断。
    if new_status is not None:
        try:
            db.refresh(t)
        except Exception:  # noqa: BLE001 - 对象已删除等场景不阻断后续写入
            pass
        if t.status == TaskStatus.cancelled:
            return
    # 进入 running 时自动刷新心跳
    if fields.get("status") == TaskStatus.running and "last_heartbeat_at" not in fields:
        fields.setdefault("last_heartbeat_at", now())
    for k, v in fields.items():
        setattr(t, k, v)
    db.commit()


def heartbeat(db, task_id):
    """单独刷新心跳（不改动 status/progress），供长任务中间打点用。"""
    t = db.get(Task, task_id)
    if not t:
        return
    t.last_heartbeat_at = now()
    db.commit()


@contextmanager
def heartbeat_guard(task_id: str, interval: int = 15):
    """后台线程定期刷新心跳的上下文管理器。

    供同步长任务（LLM 分析、BGM/SFX 生成等无中间进度的任务）使用：
    with heartbeat_guard(task_id):
        do_long_work()  # 期间每 interval 秒刷新 last_heartbeat_at

    用独立 Session 避免与任务主 Session 事务冲突；线程内异常不中断主任务。
    线程池 worker（--pool=threads）下安全；prefork 下也安全（独立线程+独立连接）。
    """
    from app.database import SessionLocal

    stop = threading.Event()

    def _loop():
        # 第一次立即打点，之后按 interval 间隔
        while not stop.wait(interval):
            try:
                _db = SessionLocal()
                t = _db.get(Task, task_id)
                if t and t.status == TaskStatus.running:
                    t.last_heartbeat_at = now()
                    _db.commit()
                _db.close()
            except Exception:
                # 心跳失败不能影响主任务
                pass

    th = threading.Thread(target=_loop, daemon=True, name=f"hb-{task_id}")
    th.start()
    try:
        yield
    finally:
        stop.set()
        th.join(timeout=2)


def run_with_polling(db, task_id, provider, handle, poll_interval: int, timeout: int):
    """循环 getTaskResult 直至 succeeded/failed/超时。

    若任务行已被级联删除（项目删除），立即退出，避免 solo worker 被幽灵任务阻塞。
    若任务被用户取消（status=cancelled），立即退出。

    2026-08-10 真实进度：handle.meta 带 ComfyUI client_id 时启动 WebSocket
    watcher 订阅该任务的采样进度写 task.progress（10~90），替换旧的时间估算。
    仅当 60s 无真实进度更新时（WS 异常/非 ComfyUI provider）才按时间估算兜底。
    """
    watcher = None
    meta = handle.meta or {}
    client_id = meta.get("client_id")
    if client_id and getattr(provider, "provider_type", None) == "comfyui":
        try:
            from app.providers.comfyui_progress import ComfyUIProgressWatcher

            endpoint = provider._endpoint()  # noqa: SLF001 同 provider 内部约定
            watcher = ComfyUIProgressWatcher(
                endpoint, client_id, handle.providerTaskId, task_id
            )
            watcher.start()
        except Exception as e:
            logger.warning("[polling] 进度监听启动失败（不影响生成）: %s", e)
            watcher = None
    try:
        return _poll_loop(db, task_id, provider, handle, poll_interval, timeout)
    finally:
        if watcher is not None:
            watcher.stop()


def _poll_loop(db, task_id, provider, handle, poll_interval: int, timeout: int):
    start = time.time()
    seen_progress = 0  # 最近一次见到的进度（watcher 写的真实进度）
    last_move = start  # 真实进度最后更新时刻
    while time.time() - start < timeout:
        t = db.get(Task, task_id)
        # 任务行可能已被级联删除（项目删除后 Task CASCADE），此时无需继续轮询
        if t is None:
            raise TaskCancelledError("任务已被取消（关联项目已删除）")
        # 用户主动取消 → 立即退出，不回写覆盖 cancelled
        if t.status == TaskStatus.cancelled:
            raise TaskCancelledError("任务已被用户取消")
        result = provider.getTaskResult(handle)
        if result.status == ProviderStatus.succeeded:
            return result
        if result.status == ProviderStatus.failed:
            raise ProviderError(result.error or "任务失败")
        elapsed = time.time() - start
        # 进度：watcher 实时进度优先；60s 无更新则按时间估算兜底（只增不减）
        db.expire(t)  # 强制刷新，拿到 watcher 独立 Session 写入的最新值
        cur = t.progress or 0
        if cur > seen_progress:
            seen_progress = cur
            last_move = time.time()
        if cur < 90 and time.time() - last_move > 60:
            est = min(89, int(elapsed / timeout * 90))
            if est > cur:
                update_task(
                    db, task_id, status=TaskStatus.running,
                    progress=est, last_heartbeat_at=now(),
                )
                seen_progress = est
        else:
            update_task(db, task_id, status=TaskStatus.running, last_heartbeat_at=now())
        time.sleep(poll_interval)
    raise ProviderError("任务超时")


def download_to_local(
    remote_url: str,
    subdir: str,
    filename: str,
    task_id: str | None = None,
    heartbeat_interval: int = 20,
) -> str:
    """下载远程 URL 到本地 media 目录，返回对外可访问 URL。

    踩坑①：httpx 下载 Agnes CDN 图片比 curl 慢 14 倍（136s vs 9s/6.6MB），
    原因是 httpx 默认 HTTP/1.1 无 h2 支持，CDN 对 HTTP/1.1 限速。改用 curl。
    踩坑②：系统代理 ICUBE_PROXY_HOST=127.0.0.1 会让 curl 走代理，7MB 图片
    60s 仅传 5MB → 超时。必须 --noproxy '*' 绕过代理直连 CDN。
    踩坑③：Agnes CDN 速度波动极大（9s~284s/7MB），--max-time 须放宽到 600s，
    并加 --retry 2 在连接慢/断时自动重试。

    超时重试：curl 默认 --retry 不重试"传输超时"（exit 28），CDN 偶发慢速时会
    直接失败。加 --retry-all-errors 让超时也重试（最多 3 次传输 × 600s）。
    单次 --max-time 放宽到 600s：实测 CDN 慢速可达 284s/7MB，400s 偏紧，
    高峰期连传 3 次都超时会整任务失败（exit 28）。

    踩坑④（2026-08-07）：Agnes CDN 速度波动极大（实测 57KB/s ~ 663KB/s），
    且单连接偶发断流。旧命令重试时从头下载（-o 覆盖写），13MB 图反复重下
    导致一张图 30 分钟。加 -C - 断点续传（CDN 实测支持 Range/206），
    重试只补剩余字节，不再浪费已下载部分。
    踩坑⑤（2026-08-07）：-C - 续传会读取"已存在的目标文件大小"。若同名单
    残留旧内容（上次任务的部分下载/旧图），续传会把新旧字节拼接成损坏文件
    （实测 broken data stream）。因此每次新下载前必须先删除已存在目标文件，
    再交给 curl（内部 --retry 时文件已由本次创建，续传安全）。

    心跳：CDN 慢时单次下载可能超过 STUCK_THRESHOLD（5 分钟）被误判卡死回收。
    传入 task_id 后，下载期间由后台线程每 heartbeat_interval 秒刷新
    last_heartbeat_at（独立 Session，与任务主事务隔离）。
    """
    from app.utils.media import clean_remote_url

    # 清洗 URL：Agnes 偶发返回 `url`（反引号包裹），不清理 curl 会解析异常
    remote_url = clean_remote_url(remote_url)
    if not remote_url:
        raise ValueError("下载地址为空")
    local_dir = os.path.join(settings.media_dir, subdir)
    os.makedirs(local_dir, exist_ok=True)
    path = os.path.join(local_dir, filename)

    stop = threading.Event()

    def _hb_loop():
        from app.database import SessionLocal

        while not stop.wait(heartbeat_interval):
            try:
                _db = SessionLocal()
                t = _db.get(Task, task_id)
                if t is not None:
                    t.last_heartbeat_at = now()
                    _db.commit()
                _db.close()
            except Exception:
                # 心跳失败不能影响下载主流程
                pass

    th = None
    if task_id:
        th = threading.Thread(target=_hb_loop, daemon=True, name=f"dl-hb-{task_id[:8]}")
        th.start()
    try:
        # 踩坑⑤：-C - 续传按文件已有大小偏移，残留文件会拼接损坏 → 先删干净
        if os.path.exists(path):
            os.remove(path)
        subprocess.run(
            ["curl", "-sS", "-L", "--noproxy", "*", "-C", "-", "--fail",
             "--retry", "2", "--retry-all-errors", "--retry-delay", "2",
             "--max-time", "600", "-o", path, remote_url],
            check=True, capture_output=True, timeout=1900,
        )
    except subprocess.CalledProcessError as e:
        # 包装成清晰的中文错误：CDN 慢速/断连（exit 28 传输超时）是主因
        detail = (e.stderr or b"").decode(errors="replace").strip()[:300]
        raise ProviderError(
            f"图片下载失败（CDN 慢速或断连）：{detail or 'curl 退出码 ' + str(e.returncode)}"
        ) from e
    finally:
        if th is not None:
            stop.set()
            th.join(timeout=2)

    # 2026-08-10：下载的图片原样落盘，不做任何 PIL 重存或 ICC 注入。
    # 用户明确要求 ComfyUI 服务器原图原封不动直接使用，任何后处理
    # （染底色/压高光/注入 ICC）都可能引入与原图的色差，已全部移除。
    # 底色统一改由 prompt 提示词在生成阶段控制。
    return f"{settings.static_base_url}/media/{subdir}/{filename}"


def bytes_to_local(b64_data: str, subdir: str, filename: str) -> str:
    """将 base64 编码的二进制数据落盘到 media 目录，返回对外可访问 URL。

    用于 TTS 适配器返回的 inline 音频 bytes（非 URL）。
    """
    local_dir = os.path.join(settings.media_dir, subdir)
    os.makedirs(local_dir, exist_ok=True)
    path = os.path.join(local_dir, filename)
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return f"{settings.static_base_url}/media/{subdir}/{filename}"


def probe_duration(local_path: str) -> float | None:
    """用 ffprobe 探测本地媒体文件时长（秒），失败返回 None。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", local_path],
            check=True, capture_output=True, timeout=15,
        )
        return float(r.stdout.decode().strip())
    except Exception:
        return None


def write_srt(subs: list, segment_id) -> str:
    """把 Subtitle 列表写成临时 .srt 文件，返回路径（调用方负责删除）。"""
    def _ms_to_ts(ms: int) -> str:
        h, ms = divmod(ms, 3600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    fd, path = tempfile.mkstemp(suffix=f"_{segment_id}.srt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for i, sub in enumerate(subs, 1):
            f.write(f"{i}\n{_ms_to_ts(sub.start_ms)} --> {_ms_to_ts(sub.end_ms)}\n{sub.text}\n\n")
    return path
