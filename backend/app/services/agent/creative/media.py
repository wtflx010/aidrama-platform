"""创作对接层 · 媒体工具：生图（含视觉 QC）/ 生视频 / TTS / 同步轮询 / 参考图处理。

从 agent_service.py 剥离（原行号 3524~3971 区域），逻辑未改动。
"""

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.providers.base import ImageOpts, ProviderStatus
from app.providers.errors import ProviderError
from app.providers.registry import ProviderRegistry
from app.services.agent.engine.constants import _CHAT_TOTAL_TIMEOUT
from app.services.agent.engine.models import _resolve_chat_model, _resolve_image_model

logger = logging.getLogger(__name__)


def _poll_sync(provider, handle, timeout: int = 300, poll_interval: int = 3):
    """同步轮询 provider 任务结果（agent 生图用，不依赖 Task 表）。"""
    start = time.time()
    while time.time() - start < timeout:
        result = provider.getTaskResult(handle)
        if result.status == ProviderStatus.succeeded:
            return result
        if result.status == ProviderStatus.failed:
            raise ProviderError(result.error or "任务失败")
        time.sleep(poll_interval)
    raise ProviderError("生成超时")


def _ensure_local_ref(image: str, session_id) -> str:
    """把参考图转为可交给 provider 的 URL：
    - data URI → 解码落盘到 media/agent/{session}/ref_{n}.png，返回本地 /static/media/ URL
      （ComfyUI 只认本地 media URL；Agnes 侧会自动转回 data URI）
    - 其他（/static/media/ 本地 URL / 公网 URL）→ 原样返回
    """
    if not image:
        return image
    if not image.startswith("data:image/"):
        return image
    try:
        import base64

        from app.tasks.base import bytes_to_local
        header, _, b64 = image.partition(",")
        ext = "png"
        if "jpeg" in header or "jpg" in header:
            ext = "jpg"
        elif "webp" in header:
            ext = "webp"
        raw = base64.b64decode(b64)
        return bytes_to_local(
            base64.b64encode(raw).decode(), subdir=f"agent/{session_id}",
            filename=f"ref_{uuid.uuid4().hex[:8]}.{ext}",
        )
    except Exception as e:
        logger.warning("参考图 data URI 落盘失败: %s", e)
        return image


def _vision_qc(image_url: str, prompt: str) -> bool:
    """视觉 QC：用对话模型（agnes-2.0-flash 支持视觉）检查生成图是否有明显瑕疵。

    失败/模型不支持视觉时静默放行（QC 是增强项，不阻断生成）。
    """
    try:
        db = SessionLocal()
        try:
            model = _resolve_chat_model(db, None)
            from app.utils.media import media_url_to_data_uri
            data_uri = media_url_to_data_uri(image_url) or image_url
            provider = ProviderRegistry.for_model(model)
            resp = provider.chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "检查这张 AI 生成的图片（用户要求：「{p}」）。"
                                    "是否有明显瑕疵：大面积马赛克/色块、人脸或主体严重扭曲变形、"
                                    "乱码文字？只回答「合格」或「不合格」。".format(p=(prompt or "")[:120])
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ]
            )
            answer = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ok = "不合格" not in answer
            logger.info("生图视觉 QC: %s（模型判定: %s）", "通过" if ok else "不合格", answer[:60])
            return ok
        finally:
            db.close()
    except Exception as e:
        logger.warning("视觉 QC 跳过（模型不支持或调用失败）: %s", e)
        return True


def _tool_generate_image(
    db: Session, session_id, args: dict, refs: list[str] | None = None,
    events: list[dict] | None = None, timeout_budget: int = _CHAT_TOTAL_TIMEOUT,
) -> str:
    """生图：有参考图 → 图生图（img2img 场景模型）；无参考图 → 文生图（keyframe 场景模型）。
    生成后视觉 QC 不合格自动重试（最多 2 次，防止明显瑕疵直接交付）。
    events 非空时，QC 重试/阶段变化会追加 tool 步骤事件（前端展示细分进度）。
    timeout_budget：单次轮询不得超过该预算（与对话总超时对齐，防 SSE 连接被无限占用）。
    """
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("生图 prompt 不能为空")
    ratio = args.get("aspect_ratio") or "16:9"
    opts = ImageOpts(ratio=ratio, negative_prompt=None)

    local_refs: list[str] = []
    if refs:
        local_refs = [_ensure_local_ref(u, session_id) for u in refs if u]

    def _step(text: str) -> None:
        if events is not None:
            events.append({"type": "tool", "name": "generate_image", "status": "running", "step": text})

    def _generate_once() -> str:
        if local_refs:
            _step("上传参考图并生成中…")
            model = _resolve_image_model(db, "img2img")
            provider = ProviderRegistry.for_model(model)
            handle = provider.imageToImage(prompt, local_refs, opts)
        else:
            _step("文生图生成中…")
            model = _resolve_image_model(db, "keyframe")
            provider = ProviderRegistry.for_model(model)
            handle = provider.textToImage(prompt, opts)
        result = _poll_sync(provider, handle, timeout=min(480, timeout_budget))
        if not result.imageUrls:
            raise ProviderError("生图完成但未返回图片 URL")
        from app.tasks.base import download_to_local
        filename = f"img_{uuid.uuid4().hex[:8]}.png"
        return download_to_local(
            result.imageUrls[0], subdir=f"agent/{session_id}", filename=filename,
        )

    url = _generate_once()
    for _ in range(2):
        if _vision_qc(url, prompt):
            return url
        logger.warning("生图 QC 不合格，重新生成一次")
        _step("质检未通过，重新生成中…")
        url = _generate_once()
    return url


def _tool_generate_video(db: Session, args: dict, refs: list[str] | None = None) -> tuple[str, str]:
    """生视频：复用 Video Lab 生成链路（创建草稿 + 派发 Celery 异步任务）。

    refs 非空 → 图生视频/多图参考生视频（参考图作为首帧/画面参考，走 R2V 链路）；
    refs 为空 → 纯文生视频（FL2VA 链路）。
    返回 (draft_id, task_id)。
    """
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("生视频 prompt 不能为空")
    ratio = args.get("aspect_ratio") or "16:9"
    # 2026-08-17 修复：VideoDraftCreate.duration 只允许 4/5/8/10/15，
    # 而 dsh 插件描述允许 2-15 → 模型传 3 等会 400。这里归整到最近合法值。
    from app.schemas.video_draft import VideoDraftCreate
    from app.services import video_draft_service

    _DURATIONS = (4, 5, 8, 10, 15)
    try:
        duration_raw = int(args.get("duration") or 5)
    except (TypeError, ValueError):
        duration_raw = 5
    duration = min(_DURATIONS, key=lambda d: abs(d - duration_raw))
    # 参考图：图生视频/多图参考生视频（data URI / 媒体 URL 原样传给草稿，生成链路自行处理）。
    # 2026-08-17 修复：ref_image_urls 显式 None 会触发 pydantic list 校验失败 → 传 list。
    ref_image_urls = [u for u in (refs or []) if u]
    draft = video_draft_service.create(
        db, VideoDraftCreate(
            project_id=None, prompt=prompt,
            aspect_ratio=ratio, duration=duration,
            ref_image_urls=ref_image_urls,
        )
    )
    task = video_draft_service.generate(db, draft.id)
    return str(draft.id), str(task.id)


def _tool_tts_speak(db: Session, args: dict, events: list[dict] | None = None) -> str:
    """TTS 语音输出（P8 Phase 4）：把一段文本朗读为语音并落盘，返回可播放音频 URL。

    - 复用配音链路的 TTS 模型（ModelType.tts + scene_code=voice）与 OpenAITTSProvider
    - voice 参数可选：default=默认声线 / preset_* 预置声线（与配音预置声音一致）
    - emotion 参数可选：走 CosyVoice instruct 情绪指令
    - events 非空时追加分阶段步骤事件（前端实时展示「准备→合成→保存」过程）
    - 失败时抛 ProviderError，由工具执行器上层统一转中文错误
    """
    from app.models.model_config import ModelType
    from app.providers.base import TTSOpts
    from app.services.keyframe_service import _resolve_model

    def _step(text: str) -> None:
        if events is not None:
            events.append({"type": "tool", "name": "tts_speak", "status": "running", "step": text})

    text = (args.get("text") or "").strip()
    if not text:
        raise ValueError("朗读文本不能为空")
    if len(text) > 2000:
        raise ValueError("朗读文本过长（最多 2000 字）")

    voice = (args.get("voice") or "").strip() or "default"
    emotion = args.get("emotion") or None

    _step("准备语音模型…")
    model = _resolve_model(db, None, ModelType.tts, "voice")
    provider = ProviderRegistry.for_model(model)
    opts = TTSOpts(voice=voice, emotion=emotion, response_format="mp3")
    _step("语音合成中…")
    handle = provider.synthesize(text, voice, opts)

    if not handle.meta.get("audio_bytes_b64"):
        raise ProviderError("TTS 合成失败：未返回音频数据")

    _step("音频保存中…")
    from app.tasks.base import bytes_to_local
    fmt = handle.meta.get("format", "mp3")
    return bytes_to_local(
        handle.meta["audio_bytes_b64"],
        subdir=f"agent_tts/{uuid.uuid4().hex[:8]}",
        filename=f"tts.{fmt}",
    )
