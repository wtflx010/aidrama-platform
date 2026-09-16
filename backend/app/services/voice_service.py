"""配音业务服务。

差异化配音（P2）：
- 按 segment.dialogue_lines 产 N 条对白 VoiceLine（每条带 character_id/emotion/instruct_text）
- 按 segment.narration 产 1 条旁白 VoiceLine（character_id=None，emotion=segment.emotion）
- 回退：无 dialogue_lines 时用 dialogue 字符串 + 主角色生成单条
- 无任何文本 → 报错

P3 增强：voice_profile 为空的角色自动触发 LLM 推荐声线，recommend 失败时
回退到基于描述关键词的预设声线（fallback_profile_by_description），确保不同
角色至少有性别/年龄差异，不至于全部用 default 声音。

每条 VoiceLine 对应一个 Task（复用 generate_voice Celery 任务）。
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.media import MediaStatus
from app.models.model_config import ModelType
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.models.voice import VoiceLine
from app.schemas.voice import VoiceGenerate
from app.services import character_voice_service
from app.services.keyframe_service import _resolve_model


def list_by_segment(db: Session, segment_id):
    """按 segment_id 列出 VoiceLine，按 line_index 排序（旁白在最后）。"""
    return db.scalars(
        select(VoiceLine)
        .where(VoiceLine.segment_id == segment_id)
        .order_by(VoiceLine.line_index.asc().nulls_last(), VoiceLine.created_at.asc())
    ).all()


def _ensure_voice_profiles(db: Session, character_ids: list[uuid.UUID]) -> None:
    """确保所有角色都有 voice_profile，空则自动推荐，推荐失败回退预设。

    P3：避免 voice_profile 为空时所有角色用 default 声音导致无差异化。
    对每个 voice_profile 为空的角色：
    1. 尝试 LLM recommend_voice_profile → apply_voice_profile
    2. 失败则用 fallback_profile_by_description 生成预设声线并写入
    """
    if not character_ids:
        return
    seen: set[uuid.UUID] = set()
    for cid in character_ids:
        if cid in seen:
            continue
        seen.add(cid)
        asset = db.get(Asset, cid)
        if not asset or asset.voice_profile:
            continue
        # voice_profile 为空：尝试 LLM 推荐
        try:
            profile = character_voice_service.recommend_voice_profile(db, cid)
            character_voice_service.apply_voice_profile(db, cid, profile)
            logging.info("角色 %r 自动推荐声线成功", asset.name)
        except Exception as e:
            # LLM 推荐失败：回退预设声线
            logging.warning(
                "角色 %r LLM 推荐声线失败(%s)，回退预设声线", asset.name, e,
            )
            fallback = character_voice_service.fallback_profile_by_description(
                asset.name, asset.description
            )
            try:
                character_voice_service.apply_voice_profile(db, cid, fallback)
            except Exception as e2:
                logging.error("角色 %r 预设声线写入也失败：%s", asset.name, e2)


def _build_voiceline(
    db: Session,
    segment: Segment,
    *,
    text: str,
    character_id: uuid.UUID | None,
    emotion: str | None,
    instruct_text: str | None,
    voice_profile: dict,
    line_index: int,
    is_narration: bool,
    model,
) -> tuple[VoiceLine, Task]:
    """构造单条 VoiceLine + Task（不派发 Celery，由调用方统一派发）。"""
    opts = character_voice_service._build_tts_opts(
        voice_profile, emotion=emotion, instruct_text=instruct_text
    )
    vl = VoiceLine(
        segment_id=segment.id,
        text=text,
        voice_id=opts.voice,
        model_id=model.id,
        status=MediaStatus.pending,
        character_id=character_id,
        emotion=emotion,
        # 用 _build_tts_opts 合成的 instruct_text（含声线描述+情绪指令）
        # 而非调用方传入的 None，确保 generate_voice 任务能拿到完整指令
        instruct_text=opts.instruct_text,
        line_index=line_index,
        is_narration=is_narration,
    )
    db.add(vl)
    db.flush()

    task = Task(
        project_id=segment.episode.project_id,
        type=TaskType.generate_voice,
        target_type="voiceline",
        target_id=vl.id,
        model_id=model.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.flush()
    vl.task_id = task.id
    return vl, task


def generate(
    db: Session,
    segment_id,
    payload: VoiceGenerate | None = None,
) -> list[tuple[VoiceLine, Task]]:
    """为分镜生成差异化配音（多条 VoiceLine）。

    返回 [(VoiceLine, Task), ...]，按 line_index 排序。
    payload 可选（保留兼容）；payload.model_id 可指定 TTS 模型。

    重新生成时清理该分镜下所有旧 VoiceLine（含关联的 pending/running Task），
    避免新旧行累积。已 succeeded 的旧音频文件不删（由定期清理回收）。
    """
    from app.tasks.generate_voice import generate_voice

    segment = db.get(Segment, segment_id)
    if not segment:
        raise ValueError("分镜不存在")

    model_id = payload.model_id if payload else None
    model = _resolve_model(db, model_id, ModelType.tts, "voice")

    # 清理旧 VoiceLine + 关联 Task（重新生成场景）
    old_lines = db.scalars(
        select(VoiceLine).where(VoiceLine.segment_id == segment_id)
    ).all()
    if old_lines:
        old_ids = [vl.id for vl in old_lines]
        # 取消关联的 pending/running Task，避免 worker 处理已删除的 VoiceLine
        db.query(Task).filter(
            Task.target_type == "voiceline",
            Task.target_id.in_(old_ids),
            Task.status.in_([TaskStatus.pending, TaskStatus.running]),
        ).update(
            {Task.status: TaskStatus.cancelled, Task.error: "重新生成，旧任务已取消"},
            synchronize_session=False,
        )
        # 删除旧 VoiceLine
        for vl in old_lines:
            db.delete(vl)
        db.flush()

    # P3：收集所有涉及的角色 ID，确保 voice_profile 非空（自动推荐 + 预设回退）
    dialogue_lines = segment.dialogue_lines or []
    all_char_ids: list[uuid.UUID] = []
    for line in dialogue_lines:
        if isinstance(line, dict):
            cid = line.get("character_id")
            if cid:
                all_char_ids.append(cid)
    if not dialogue_lines and segment.dialogue:
        # 回退路径：用主角色
        all_char_ids = list(segment.character_ids or [])
    _ensure_voice_profiles(db, all_char_ids)

    lines_to_generate: list[dict] = []

    # 1) 结构化对白：每条 dialogue_line → 独立 VoiceLine
    if dialogue_lines:
        for i, line in enumerate(dialogue_lines):
            if not isinstance(line, dict):
                continue
            text = (line.get("text") or "").strip()
            if not text:
                continue
            char_id = line.get("character_id")
            emotion = line.get("emotion")
            # 取角色声线档案
            if char_id:
                vp = character_voice_service.get_character_voice_profile(db, char_id)
                # 角色无 voice_profile 时用默认情绪兜底
                if not emotion and vp:
                    emotion = vp.get("default_emotion")
            else:
                vp = {}
            lines_to_generate.append({
                "text": text,
                "character_id": char_id,
                "emotion": emotion,
                "instruct_text": None,  # 由 Provider 层按 emotion 映射
                "voice_profile": vp,
                "line_index": i,
                "is_narration": False,
            })
    elif segment.dialogue:
        # 回退：无 dialogue_lines 但有 dialogue 字符串 → 用主角色生成单条
        char_ids = segment.character_ids or []
        char_id = char_ids[0] if char_ids else None
        vp = (
            character_voice_service.get_character_voice_profile(db, char_id)
            if char_id else {}
        )
        lines_to_generate.append({
            "text": segment.dialogue,
            "character_id": char_id,
            "emotion": segment.emotion or vp.get("default_emotion"),
            "instruct_text": None,
            "voice_profile": vp,
            "line_index": 0,
            "is_narration": False,
        })

    # 2) 旁白：narration 非空 → 一条 VoiceLine，用项目旁白声线
    if segment.narration and segment.narration.strip():
        narrator_vp = character_voice_service.get_narrator_profile(
            db, segment.episode.project_id
        )
        lines_to_generate.append({
            "text": segment.narration.strip(),
            "character_id": None,
            "emotion": segment.emotion or narrator_vp.get("default_emotion"),
            "instruct_text": None,
            "voice_profile": narrator_vp,
            "line_index": len(lines_to_generate),
            "is_narration": True,
        })

    if not lines_to_generate:
        raise ValueError("分镜无对白/旁白，无法生成配音")

    # 3) 批量建 VoiceLine + Task
    results: list[tuple[VoiceLine, Task]] = []
    for item in lines_to_generate:
        vl, task = _build_voiceline(
            db, segment,
            text=item["text"],
            character_id=item["character_id"],
            emotion=item["emotion"],
            instruct_text=item["instruct_text"],
            voice_profile=item["voice_profile"],
            line_index=item["line_index"],
            is_narration=item["is_narration"],
            model=model,
        )
        results.append((vl, task))

    db.commit()
    for vl, task in results:
        db.refresh(vl)
        db.refresh(task)

    # 4) 派发 Celery 任务
    for vl, task in results:
        generate_voice.delay(str(task.id))

    return results
