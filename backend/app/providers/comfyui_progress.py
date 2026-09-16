"""ComfyUI WebSocket 实时进度监听（写 task.progress）。

背景：旧逻辑按"已耗时间 / 超时"线性估算进度，与实际完成度不符（用户反馈
进度条是假的）。ComfyUI 的 progress 事件只推送给提交时匹配 client_id 的连接
（server.py 的 send_sync sid 路由），因此任务提交时（provider._submit）生成
唯一 client_id，轮询期间用同一 client_id 连接 /ws 订阅该任务的采样进度，
把 value/max（KSampler 步数）映射到 10~90 写库，替换假进度。

下载/落库阶段（90~100）由任务侧在下载完成后置 100。
"""
import asyncio
import json
import logging
import threading

from app.database import SessionLocal
from app.models.task import Task

logger = logging.getLogger(__name__)

# 采样阶段映射区间（提交=10 起点，生成完成=90，下载/落库=90~100 由任务侧覆盖）
_PROGRESS_LOW = 10
_PROGRESS_HIGH = 90


def _to_ws_url(endpoint: str) -> str:
    ep = (endpoint or "").rstrip("/")
    if ep.startswith("https://"):
        return ep.replace("https://", "wss://")
    if ep.startswith("http://"):
        return ep.replace("http://", "ws://")
    return f"ws://{ep}"


def _write_progress(task_id: str, progress: int) -> None:
    """独立 Session 写进度（只增不减，线程安全，失败静默）。"""
    try:
        db = SessionLocal()
        try:
            t = db.get(Task, task_id)
            if t is not None and (t.progress or 0) < progress:
                t.progress = progress
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.debug("[progress] 写进度失败 task=%s: %s", task_id, e)


class ComfyUIProgressWatcher:
    """后台线程监听单个 ComfyUI 任务的采样进度并写库。"""

    def __init__(self, endpoint: str, client_id: str, prompt_id: str, task_id: str):
        self.ws_url = f"{_to_ws_url(endpoint)}/ws?clientId={client_id}"
        self.prompt_id = prompt_id
        self.task_id = str(task_id)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="comfyui-progress"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._listen())
        except Exception as e:
            logger.debug("[progress] 监听结束 task=%s: %s", self.task_id, e)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _listen(self) -> None:
        import websockets

        async with websockets.connect(self.ws_url, open_timeout=5) as ws:
            while not self._stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                except asyncio.TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    continue
                try:
                    data = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                mtype = data.get("type")
                d = data.get("data") or {}
                if mtype == "progress" and d.get("prompt_id") == self.prompt_id:
                    value, mx = d.get("value"), d.get("max")
                    if isinstance(value, int) and isinstance(mx, int) and mx > 0:
                        pct = min(
                            _PROGRESS_HIGH,
                            max(
                                _PROGRESS_LOW,
                                _PROGRESS_LOW
                                + int((_PROGRESS_HIGH - _PROGRESS_LOW) * value / mx),
                            ),
                        )
                        _write_progress(self.task_id, pct)
                elif (
                    mtype == "executing"
                    and d.get("prompt_id") == self.prompt_id
                    and d.get("node") is None
                ):
                    # executing(node=null) 表示该 prompt 执行完成
                    _write_progress(self.task_id, _PROGRESS_HIGH)
                    break
