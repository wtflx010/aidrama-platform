"""成片评估 API（P0-1）：触发评估 / 查询最新评估 / 反哺闭环。

对标 Higgsfield Virality Predictor：对一集成片打四维分，并支持把评估建议
应用到分镜（清提示词缓存 + 可重新派发关键帧生成），形成「评估→反哺→重出」闭环。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.episode_eval import EvalStatus
from app.models.project import Episode
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.episode_eval import (
    ApplyFeedbackIn,
    ApplyFeedbackOut,
    EpisodeEvalOut,
)
from app.services import evaluate_service

router = APIRouter()


@router.post("/projects/{project_id}/episodes/{episode_id}/evaluate", status_code=201)
def trigger_evaluate(project_id: UUID, episode_id: UUID, db: Session = Depends(get_db)):
    """触发一集成片评估（异步任务：规则分 + LLM 四维分 + 反哺建议）。"""
    episode = db.get(Episode, episode_id)
    if not episode:
        raise HTTPException(404, "幕不存在")
    if episode.project_id != project_id:
        raise HTTPException(404, "幕不属于该项目")

    task = Task(
        project_id=project_id,
        type=TaskType.evaluate_episode,
        target_type="episode",
        target_id=episode_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from app.tasks.evaluate_episode import evaluate_episode
    evaluate_episode.delay(str(task.id), str(episode_id))
    return {"task_id": str(task.id), "episode_id": str(episode_id)}


@router.get("/projects/{project_id}/episodes/{episode_id}/evaluate", response_model=EpisodeEvalOut | None)
def get_evaluate(project_id: UUID, episode_id: UUID, db: Session = Depends(get_db)):
    """查询一集最新评估结果。"""
    episode = db.get(Episode, episode_id)
    if not episode or episode.project_id != project_id:
        raise HTTPException(404, "幕不存在")
    row = evaluate_service.latest(db, episode_id)
    if row is None:
        return None
    return EpisodeEvalOut(
        id=row.id, episode_id=row.episode_id, video_url=row.video_url,
        scores=row.scores, rule_scores=row.rule_scores, report=row.report,
        suggestions=row.suggestions, status=row.status.value, error=row.error,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


@router.get("/projects/{project_id}/episodes/{episode_id}/consistency")
def get_consistency(project_id: UUID, episode_id: UUID, db: Session = Depends(get_db)):
    """P0-2 角色一致性总览：消费 H3 retention_analysis，标出弱保留分镜供反哺重出。"""
    episode = db.get(Episode, episode_id)
    if not episode or episode.project_id != project_id:
        raise HTTPException(404, "幕不存在")
    from app.services.consistency_service import episode_consistency
    return episode_consistency(episode)


@router.post("/projects/{project_id}/episodes/{episode_id}/evaluate/apply-feedback",
             response_model=ApplyFeedbackOut)
def apply_feedback(episode_id: UUID, payload: ApplyFeedbackIn, db: Session = Depends(get_db)):
    """把评估建议应用到分镜，形成闭环。

    - 清指定分镜（默认=评估建议涉及的全部分镜）的增强提示词缓存，
      下次生成自动用新提示词；
    - regenerate_keyframes=True 时为这些分镜重新派发关键帧生成（刷新视觉锚点）。
    """
    episode = db.get(Episode, episode_id)
    if not episode:
        raise HTTPException(404, "幕不存在")
    row = evaluate_service.latest(db, episode_id)
    if row is None or row.status != EvalStatus.succeeded:
        raise HTTPException(400, "该幕尚无成功的评估结果，请先触发评估")

    if payload.segment_indexes:
        indexes = set(payload.segment_indexes)
    else:
        indexes = {s["segment_index"] for s in row.suggestions}

    from app.schemas.keyframe import KeyframeGenerate
    from app.services import keyframe_service
    from app.services.prompt_enhance_service import clear_enhanced_prompt

    applied = []
    for seg in episode.segments:
        if seg.index not in indexes:
            continue
        clear_enhanced_prompt(db, seg)
        kf_task_id = None
        if payload.regenerate_keyframes:
            try:
                _, task = keyframe_service.generate(db, seg.id, KeyframeGenerate(use_reference=True))
                kf_task_id = str(task.id)
            except ValueError as e:
                kf_task_id = None  # 资产缺参考等失败不阻断其他分镜
        applied.append({"segment_index": seg.index, "keyframe_task_id": kf_task_id})
    db.commit()
    return ApplyFeedbackOut(ok=True, applied=applied,
                            message=f"已处理 {len(applied)} 个分镜" if applied else "没有需要处理的分镜")
