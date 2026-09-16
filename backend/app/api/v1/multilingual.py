"""多语言配音与字幕导出 API（P2-5）。

POST /projects/{pid}/episodes/{eid}/multilingual 触发 dub_episode 任务；
GET  .../multilingual/tasks 列出该幕历史多语言导出任务（异步轮询用）。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Episode
from app.models.task import Task, TaskStatus, TaskType

router = APIRouter()


class MultilingualIn(BaseModel):
    target_language: str = "eng"
    do_tts: bool = True


@router.post("/projects/{project_id}/episodes/{episode_id}/multilingual", status_code=201)
def trigger_multilingual(project_id: UUID, episode_id: UUID, payload: MultilingualIn,
                         db: Session = Depends(get_db)):
    """触发一集的多语言配音+字幕导出（异步）。target_language: ISO 639-3。"""
    episode = db.get(Episode, episode_id)
    if not episode or episode.project_id != project_id:
        raise HTTPException(404, "幕不存在")
    task = Task(
        project_id=project_id, type=TaskType.dub_episode,
        target_type="episode", target_id=episode_id, status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    from app.tasks.dub_episode import dub_episode
    dub_episode.delay(str(task.id), str(episode_id), payload.target_language, payload.do_tts)
    return {"task_id": str(task.id), "episode_id": str(episode_id),
            "target_language": payload.target_language}


@router.get("/projects/{project_id}/episodes/{episode_id}/multilingual/tasks")
def list_multilingual_tasks(project_id: UUID, episode_id: UUID, db: Session = Depends(get_db)):
    """列出该幕多语言导出任务（供前端轮询结果/下载 SRT）。"""
    rows = db.scalars(
        select(Task)
        .where(Task.project_id == project_id,
               Task.type == TaskType.dub_episode,
               Task.target_id == episode_id)
        .order_by(Task.created_at.desc())
    ).all()
    return [
        {"task_id": str(t.id), "status": t.status.value,
         "error": t.error, "result_url": t.result_url,
         "target_language": t.provider,  # 暂无独立字段，暂放 provider 位（可读）
         "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in rows
    ]
