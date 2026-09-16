"""http_poll 适配器：异步提交 + 轮询（Agnes 视频等）。路径/取值全部读 Model.http_poll_config。

通用化：http_poll_config.body_template 支持自定义请求体模板，兼容 Agnes 以外的商用 API。
模板用 {variable} 占位符，变量列表见 _render_body docstring。
无 body_template 时回退 Agnes 默认格式（向后兼容）。
"""
import logging
import time

from app.providers.base import (
    BaseProvider,
    ProviderStatus,
    TaskHandle,
    TaskResult,
    VideoOpts,
)
from app.providers.errors import ProviderError, map_to_chinese
from app.utils.jsonpath import jsonpath_get
from app.utils.media import media_url_to_data_uri

logger = logging.getLogger(__name__)

# Agnes 视频队列满（503 video_queue_full）：服务端队列临时打满，提交级自动退避重试，
# 避免任务直接失败。间隔递增，总等待约 5 分钟。
_QUEUE_FULL_MAX_RETRIES = 5
_QUEUE_FULL_BACKOFF = (20, 40, 80, 160)

_STATUS_MAP = {
    "pending": ProviderStatus.running,
    "processing": ProviderStatus.running,
    "running": ProviderStatus.running,
    "queued": ProviderStatus.running,
    "succeeded": ProviderStatus.succeeded,
    "success": ProviderStatus.succeeded,
    "completed": ProviderStatus.succeeded,
    "failed": ProviderStatus.failed,
    "error": ProviderStatus.failed,
}


class HttpPollProvider(BaseProvider):
    provider_type = "http_poll"

    def imageToVideo(
        self, firstFrame: str, lastFrame: str | None, opts: VideoOpts,
        reference_assets: list[str] | None = None,
    ) -> TaskHandle:
        # Agnes 不接受 localhost/私有网络 URL，需把本地 media URL 转 base64 data URI
        firstFrame = media_url_to_data_uri(firstFrame)
        if lastFrame:
            lastFrame = media_url_to_data_uri(lastFrame)
        cfg = self.cfg.http_poll_config or {}
        url = f"{self.cfg.endpoint}{cfg['submit_path']}"

        body_template = cfg.get("body_template")
        if body_template:
            # 通用模式：用 body_template 渲染请求体（接可灵/Runway/即梦等）
            body = self._render_body(body_template, firstFrame, lastFrame, opts)
        else:
            # Agnes 默认格式（向后兼容）
            body = {
                "model": self.cfg.model_id,
                "prompt": opts.prompt,
                "image": firstFrame,
                "width": opts.width,
                "height": opts.height,
                "num_frames": opts.num_frames,
                "frame_rate": opts.frame_rate,
            }
            if lastFrame:
                body["image_tail"] = lastFrame

        # 提交时队列满（503 video_queue_full）→ 退避重试；耗尽后给出明确中文提示
        last_resp = None
        for attempt in range(_QUEUE_FULL_MAX_RETRIES):
            last_resp = self.http.post(url, headers=self._auth(), json=body, timeout=240)
            if not self._is_queue_full(last_resp):
                break
            if attempt < _QUEUE_FULL_MAX_RETRIES - 1:
                wait = _QUEUE_FULL_BACKOFF[min(attempt, len(_QUEUE_FULL_BACKOFF) - 1)]
                logger.warning(
                    "视频队列已满（503 video_queue_full），%s 秒后自动重试（第 %d/%d 次）",
                    wait, attempt + 1, _QUEUE_FULL_MAX_RETRIES,
                )
                time.sleep(wait)
        if self._is_queue_full(last_resp):
            raise ProviderError("视频生成队列已满（video_queue_full），已自动重试多次仍未成功，请稍后再试")
        last_resp.raise_for_status()
        task_id = jsonpath_get(last_resp.json(), cfg.get("task_id_jsonpath", "$.task_id"))
        if not task_id:
            raise ProviderError("提交视频任务未返回 task_id")
        poll_url = f"{self.cfg.endpoint}{cfg['query_path'].format(task_id=task_id)}"
        return TaskHandle(
            provider=self.provider_type,
            providerTaskId=str(task_id),
            pollUrl=poll_url,
            estimatedSeconds=int(opts.duration or opts.num_frames / opts.frame_rate),
        )

    def _render_body(self, template: dict, firstFrame: str, lastFrame: str | None,
                     opts: VideoOpts) -> dict:
        """用 body_template 渲染请求体。

        模板值含 {variable} 占位符时格式化，否则直接使用（支持静态值）。
        渲染后自动尝试转 int/float/bool。

        可用变量：
          {model_id}     模型 ID（cfg.model_id）
          {prompt}       提示词
          {firstFrame}   首帧（已转 base64 data URI）
          {lastFrame}    尾帧（已转 base64，可能为空字符串）
          {width} {height} {num_frames} {frame_rate}  视频参数
          {duration}     时长（秒）
        """
        variables = {
            "model_id": self.cfg.model_id,
            "prompt": opts.prompt or "",
            "firstFrame": firstFrame or "",
            "lastFrame": lastFrame or "",
            "width": opts.width or 0,
            "height": opts.height or 0,
            "num_frames": opts.num_frames or 0,
            "frame_rate": opts.frame_rate or 24,
            "duration": opts.duration or (
                (opts.num_frames / opts.frame_rate) if opts.frame_rate else 5
            ),
        }
        body = {}
        for key, val in template.items():
            if isinstance(val, str) and "{" in val:
                body[key] = self._coerce(val.format(**variables))
            elif isinstance(val, str):
                body[key] = self._coerce(val)
            else:
                body[key] = val
        return body

    @staticmethod
    def _is_queue_full(resp) -> bool:
        """识别服务端视频队列打满（503 + video_queue_full）。"""
        if resp.status_code != 503:
            return False
        try:
            data = resp.json()
        except Exception:
            return False
        code = str(data.get("code") or "").lower()
        msg = str(data.get("message") or "").lower()
        return "queue" in code or "queue" in msg

    @staticmethod
    def _coerce(val: str):
        """尝试将字符串转为 int/float/bool，失败则返回原字符串。"""
        if val.lower() in ("true", "false"):
            return val.lower() == "true"
        try:
            return int(val)
        except ValueError:
            pass
        try:
            return float(val)
        except ValueError:
            pass
        return val

    def getTaskResult(self, handle: TaskHandle) -> TaskResult:
        cfg = self.cfg.http_poll_config or {}
        r = self.http.get(handle.pollUrl, headers=self._auth(), timeout=30)
        r.raise_for_status()
        data = r.json()
        status_raw = jsonpath_get(data, cfg.get("status_jsonpath", "$.status"))
        mapped = _STATUS_MAP.get(str(status_raw).lower(), ProviderStatus.running)
        if mapped == ProviderStatus.succeeded:
            url = jsonpath_get(data, cfg.get("result_jsonpath", "$.video_url"))
            if not url:
                # Agnes 可能在 status=completed 时 url 尚未就绪，视为仍在处理
                return TaskResult(status=ProviderStatus.running, raw=data)
            from app.utils.media import clean_remote_url
            url = clean_remote_url(url)
            return TaskResult(status=mapped, videoUrl=url, raw=data)
        if mapped == ProviderStatus.failed:
            err = data.get("error") or data.get("message") or str(data)
            return TaskResult(status=mapped, error=str(err), raw=data)
        return TaskResult(status=ProviderStatus.running, raw=data)

    def test_connection(self) -> tuple[bool, str]:
        """轻量校验：GET submit_path，401/403→密钥错，其它→端点可达。"""
        cfg = self.cfg.http_poll_config or {}
        try:
            url = f"{self.cfg.endpoint}{cfg.get('submit_path', '/videos')}"
            r = self.http.get(url, headers=self._auth(), timeout=15)
            if r.status_code in (401, 403):
                return False, "模型密钥无效或无权限"
            return True, f"OK (HTTP {r.status_code})"
        except Exception as e:
            return False, map_to_chinese(e)
