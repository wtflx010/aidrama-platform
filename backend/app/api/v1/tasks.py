"""任务 API：查询 / 按项目列表 / 取消。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.task import TaskOut
from app.services import task_service

router = APIRouter()


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_one(task_id: UUID, db: Session = Depends(get_db)):
    t = task_service.get(db, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_by_project(
    project_id: UUID,
    type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """项目任务列表；分镜级任务（生成视频/关键帧）附加 segment_ref（幕/镜序号）便于识别。"""
    items = task_service.list_by_project(db, project_id, type=type, status=status)
    outs = []
    for t in items:
        o = TaskOut.model_validate(t)
        o.segment_ref = task_service.resolve_segment_ref(db, t)
        outs.append(o)
    return outs


@router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel(task_id: UUID, db: Session = Depends(get_db)):
    t = task_service.cancel(db, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@router.post("/projects/{project_id}/tasks/retry-failed")
def retry_failed(
    project_id: UUID,
    type: str | None = None,
    db: Session = Depends(get_db),
):
    """批量重跑失败任务（可选 type 过滤，如 generate_video）。"""
    tasks, msg = task_service.retry_failed(db, project_id, type=type)
    return {"ok": True, "count": len(tasks), "message": msg, "tasks": [t.id for t in tasks]}
