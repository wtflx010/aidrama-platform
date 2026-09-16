"""TTS 配音生成任务：synthesize → 音频 bytes 落盘 → ffprobe 探时长 → 回填 VoiceLine。

差异化配音（P2）：
- 从 VoiceLine 读取 emotion/instruct_text
- 根据 character_id / is_narration 解析 voice_profile，取 prompt_wav/prompt_text
- 构造完整 TTSOpts 传给 Provider
"""
import os

from app.database import SessionLocal
from app.models.asset import Asset, AssetType
from app.models.media import MediaStatus
from app.models.model_config import Model
from app.models.task import Task, TaskStatus
from app.models.voice import VoiceLine
from app.providers.base import TTSOpts
from app.providers.errors import map_to_chinese
from app.providers.registry import ProviderRegistry
from app.services import character_voice_service
from app.tasks.base import bytes_to_local, heartbeat_guard, now, probe_duration, update_task
from app.tasks.celery_app import celery_app


def _resolve_prompt_for_voiceline(db, vl: VoiceLine) -> tuple[str | None, str | None]:
    """根据 VoiceLine 解析 prompt_wav/prompt_text。

    - is_narration=True → 取 project.narrator_profile
    - character_id 非空 → 取 asset.voice_profile
    - 否则 → (None, None)，用服务端 default 声音

    prompt_wav 转换：本地静态 URL → 文件系统路径（CosyVoice wrapper 需本地路径）。
    """
    if vl.is_narration:
        # 旁白：取项目旁白声线
        segment = vl.segment
        if segment:
            vp = character_voice_service.get_narrator_profile(
                db, segment.episode.project_id
            )
        else:
            vp = {}
    elif vl.character_id:
        vp = character_voice_service.get_character_voice_profile(db, vl.character_id)
    else:
        vp = {}
    prompt_wav = vp.get("reference_audio_url")
    prompt_text = vp.get("reference_audio_text")
    # 本地静态 URL → 文件系统路径（CosyVoice wrapper 读取本地文件）
    if prompt_wav:
        from app.config import settings
        media_prefix = f"{settings.static_base_url}/media/"
        if prompt_wav.startswith(media_prefix):
            prompt_wav = prompt_wav.replace(media_prefix, f"{settings.media_dir}/")
    return prompt_wav, prompt_text


@celery_app.task(name="generate_voice", bind=True)
def generate_voice(self, task_id: str):
    db = SessionLocal()
    target_id = None
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        target_id = task.target_id
        vl = db.get(VoiceLine, target_id)
        if vl is None:
            return
        model = db.get(Model, task.model_id)
        provider = ProviderRegistry.for_model(model)

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=10)
        vl.status = MediaStatus.running
        db.commit()

        # 差异化配音：解析 prompt_wav/prompt_text（从 voice_profile）
        prompt_wav, prompt_text = _resolve_prompt_for_voiceline(db, vl)
        opts = TTSOpts(
            voice=vl.voice_id or "default",
            emotion=vl.emotion,
            instruct_text=vl.instruct_text,
            prompt_wav=prompt_wav,
            prompt_text=prompt_text,
        )
        with heartbeat_guard(task_id, interval=10):
            handle = provider.synthesize(vl.text, vl.voice_id or opts.voice, opts)
        update_task(
            db, task_id, provider=handle.provider,
            provider_task_id=handle.providerTaskId, progress=50,
        )

        # 同步适配器：从 meta 取 base64 音频 bytes 落盘
        fmt = handle.meta.get("format", "mp3")
        local_url = bytes_to_local(
            handle.meta["audio_bytes_b64"],
            subdir=f"voicelines/{vl.id}", filename=f"voice.{fmt}",
        )
        # 探测时长：ffprobe 失败则按共享 CHARS_PER_SEC（5.5 字/秒）估算
        from app.config import settings
        from app.constants import CHARS_PER_SEC
        local_path = local_url.replace(
            f"{settings.static_base_url}/media/", f"{settings.media_dir}/"
        )
        duration = probe_duration(local_path)
        if not duration:
            duration = max(1.0, len(vl.text) / CHARS_PER_SEC)

        vl.audio_url = local_url
        vl.duration = duration
        vl.status = MediaStatus.succeeded
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=local_url, finished_at=now(),
        )
        db.commit()
    except Exception as e:
        db.rollback()
        # 取消保护：任务已被用户取消时，不把配音行回写为 failed（task_service.cancel
        # 已把 VoiceLine 回退 pending，此处回写 failed 会覆盖取消语义，前端无法重新生成）
        t = db.get(Task, task_id)
        if t is not None and t.status == TaskStatus.cancelled:
            return
        msg = map_to_chinese(e)
        update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        if target_id:
            vl = db.get(VoiceLine, target_id)
            if vl:
                vl.status = MediaStatus.failed
                vl.error = msg
                db.commit()
    finally:
        db.close()
