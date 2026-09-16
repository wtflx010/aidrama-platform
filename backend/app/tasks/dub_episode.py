"""多语言配音与字幕导出任务（P2-5）：翻译对白/旁白/字幕 → 建多语言配音 → 写 SRT。

对标 Higgsfield dubbing：一条任务完成「翻译 + TTS 配音（语言跟随文本）+ SRT 导出」。
"""
import logging
import uuid

from sqlalchemy import select

from app.database import SessionLocal
from app.models.media import MediaStatus
from app.models.model_config import ModelType
from app.models.project import Episode
from app.models.task import Task, TaskStatus, TaskType
from app.models.voice import VoiceLine
from app.services import multilingual_service
from app.tasks.base import now, update_task
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 支持的多语言白名单（ISO 639-3），不在此列直接 400/失败
_SUPPORTED = {"eng", "spa", "fra", "deu", "ita", "por", "rus", "jpn", "kor",
              "vie", "tha", "ara", "hin", "ind", "msa", "chi", "yue"}


@celery_app.task(name="dub_episode", bind=True)
def dub_episode(self, task_id: str, episode_id: str, target_lang: str = "eng", do_tts: bool = True):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        try:
            ep_uuid = uuid.UUID(str(episode_id))
        except (ValueError, TypeError):
            update_task(db, task_id, status=TaskStatus.failed, error="幕 ID 非法", finished_at=now())
            db.close()
            return
        episode = db.get(Episode, ep_uuid)
        if episode is None:
            update_task(db, task_id, status=TaskStatus.failed, error="幕不存在", finished_at=now())
            db.close()
            return
        if target_lang not in _SUPPORTED:
            update_task(db, task_id, status=TaskStatus.failed,
                        error=f"不支持的语言 {target_lang}（可用: {sorted(_SUPPORTED)}）", finished_at=now())
            db.close()
            return

        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)

        # 1) 翻译整集
        translated = multilingual_service.translate_episode(db, episode, target_lang)
        # 2) 导出翻译后 SRT（主产物）
        srt_url = multilingual_service.save_srt(episode, translated)
        dispatched = 0
        # 3) 可选：为翻译后对白建多语言配音（复用 generate_voice TTS，语言跟随文本）
        if do_tts:
            from app.services.keyframe_service import _resolve_model
            from app.tasks.generate_voice import generate_voice

            model = _resolve_model(db, None, ModelType.tts, "voice")
            seg_by_index = {s.index: s for s in episode.segments}
            for seg in translated["segments"]:
                seg_obj = seg_by_index.get(seg["segment_index"])
                if seg_obj is None:
                    continue
                for i, d in enumerate(seg["dialogue"]):
                    text = (d.get("text") or "").strip()
                    if not text:
                        continue
                    instruct = multilingual_service.build_voice_instruction(
                        target_lang, d.get("emotion")
                    )
                    vl = VoiceLine(
                        segment_id=seg_obj.id, text=text,
                        character_id=uuid.UUID(d["character_id"]) if d.get("character_id") else None,
                        emotion=d.get("emotion"),
                        instruct_text=instruct,
                        line_index=i,
                        is_narration=False,
                        language=target_lang,
                        model_id=model.id,
                        status=MediaStatus.pending,
                    )
                    db.add(vl)
                    db.flush()
                    dt = Task(
                        project_id=episode.project_id, type=TaskType.generate_voice,
                        target_type="voiceline", target_id=vl.id, model_id=model.id,
                        status=TaskStatus.pending,
                    )
                    db.add(dt)
                    db.flush()
                    vl.task_id = dt.id
                    db.commit()
                    generate_voice.delay(str(dt.id))
                    dispatched += 1
                # 旁白也出多语言配音（is_narration=True，line_index 置大使其排在对话之后）
                narration = (seg.get("narration") or "").strip()
                if narration:
                    vln = VoiceLine(
                        segment_id=seg_obj.id, text=narration, character_id=None,
                        emotion=None,
                        instruct_text=multilingual_service.build_voice_instruction(target_lang, None),
                        line_index=999, is_narration=True, language=target_lang,
                        model_id=model.id, status=MediaStatus.pending,
                    )
                    db.add(vln)
                    db.flush()
                    dt2 = Task(
                        project_id=episode.project_id, type=TaskType.generate_voice,
                        target_type="voiceline", target_id=vln.id, model_id=model.id,
                        status=TaskStatus.pending,
                    )
                    db.add(dt2)
                    db.flush()
                    vln.task_id = dt2.id
                    db.commit()
                    generate_voice.delay(str(dt2.id))
                    dispatched += 1
            db.commit()

        update_task(db, task_id, status=TaskStatus.succeeded, progress=100,
                    result_url=srt_url, finished_at=now())
        db.commit()
        logger.info("[multilingual] 幕 %s → %s 导出完成，配音 %d 条，SRT=%s",
                    episode_id, target_lang, dispatched, srt_url)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        msg = str(e)[:500]
        try:
            update_task(db, task_id, status=TaskStatus.failed, error=msg, finished_at=now())
        except Exception:  # noqa: BLE001
            pass
        db.commit()
        logger.exception("[multilingual] 幕 %s 多语言导出失败", episode_id)
    finally:
        db.close()
