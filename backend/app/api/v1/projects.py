"""项目 API：一句话生成 / 列表 / 详情 / 创建 / 更新 / 删除 / 幕列表。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.episode import EpisodeOut
from app.schemas.project import AIGenerateBody, ProjectCreate, ProjectOut, ProjectUpdate
from app.services import episode_service, project_service

router = APIRouter()


@router.post("/ai-generate", response_model=ProjectOut, status_code=201)
def ai_generate(body: AIGenerateBody, db: Session = Depends(get_db)):
    """一句话梗概 → LLM 生成项目（含剧本与分镜）。"""
    try:
        return project_service.ai_generate(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("", response_model=list[ProjectOut])
def list_projects(status: str | None = None, db: Session = Depends(get_db)):
    return project_service.list_projects(db, status=status)


@router.post("", response_model=ProjectOut, status_code=201)
def create(payload: ProjectCreate, db: Session = Depends(get_db)):
    return project_service.create(db, payload)


@router.get("/{project_id}", response_model=ProjectOut)
def get_one(project_id: UUID, db: Session = Depends(get_db)):
    p = project_service.get(db, project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.put("/{project_id}", response_model=ProjectOut)
def update_one(project_id: UUID, payload: ProjectUpdate, db: Session = Depends(get_db)):
    p = project_service.update(db, project_id, payload)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.delete("/{project_id}")
def delete_one(project_id: UUID, db: Session = Depends(get_db)):
    if not project_service.delete(db, project_id):
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


@router.get("/{project_id}/timeline", response_model=dict)
def get_project_timeline(project_id: UUID, db: Session = Depends(get_db)):
    """分镜时间线：每幕每镜起止秒数。project_timeline 按 duration 实时累计；script_timeline 为只读参考。"""
    from app.models.project import Episode, Project
    from app.models.segment import Segment
    from sqlalchemy import select
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    eps = db.scalars(select(Episode).where(Episode.project_id == project_id).order_by(Episode.index)).all()
    project_shots = []
    proj_cur = 0.0
    for ep in eps:
        segs = db.scalars(select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index)).all()
        for s in segs:
            d = max(1.0, float(s.duration or 5.0))
            project_shots.append({
                "segment_id": str(s.id),
                "episode_index": ep.index + 1,
                "segment_index": s.index + 1,
                "title": s.title,
                "start_ms": int(proj_cur * 1000),
                "end_ms": int((proj_cur + d) * 1000),
                "duration": d,
            })
            proj_cur += d
    script_shots = [dict(s) for s in project_shots]
    return {
        "total_duration_s": round(proj_cur, 1),
        "project_timeline": project_shots,
        "script_timeline": script_shots,
    }



@router.get("/{project_id}/episodes", response_model=list[EpisodeOut])
def list_episodes(project_id: UUID, db: Session = Depends(get_db)):
    """项目下的幕列表（P0 通常只有一个主幕）。"""
    return episode_service.list_by_project(db, project_id)
