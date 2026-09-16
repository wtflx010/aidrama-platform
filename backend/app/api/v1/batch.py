"""批量生成 API：整幕关键帧/视频批量生成。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.batch import BatchBody, BatchResp
from app.services import batch_service

router = APIRouter()


@router.post("/episodes/{episode_id}/keyframes/batch-generate", response_model=BatchResp, status_code=201)
def batch_keyframes(episode_id: UUID, payload: BatchBody, db: Session = Depends(get_db)):
    try:
        task, total, dispatched_ids = batch_service.batch_keyframes(db, episode_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return BatchResp(task=task, total=total, dispatched_ids=dispatched_ids)


@router.post("/episodes/{episode_id}/video/batch-generate", response_model=BatchResp, status_code=201)
def batch_videos(episode_id: UUID, payload: BatchBody, db: Session = Depends(get_db)):
    try:
        task, total, dispatched_ids = batch_service.batch_videos(db, episode_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return BatchResp(task=task, total=total, dispatched_ids=dispatched_ids)


@router.post("/projects/{project_id}/video/batch-upscale", response_model=BatchResp, status_code=201)
def batch_project_upscale(project_id: UUID, payload: BatchBody | None = None, db: Session = Depends(get_db)):
    """一键超分所有分镜（三列工作台顶栏「超分所有分镜」）。"""
    try:
        task, total, ids = batch_service.batch_project_upscales(db, project_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return BatchResp(task=task, total=total, dispatched_ids=ids)


@router.post("/projects/{project_id}/video/batch-generate", response_model=BatchResp, status_code=201)
def batch_project_videos(project_id: UUID, payload: BatchBody | None = None, db: Session = Depends(get_db)):
    """生成所有分镜视频（三列工作台右上）。"""
    try:
        task, total, ids = batch_service.batch_project_videos(db, project_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return BatchResp(task=task, total=total, dispatched_ids=ids)



@router.post("/projects/{project_id}/assets/batch-generate-cover", response_model=BatchResp, status_code=201)
def batch_asset_covers(
    project_id: UUID,
    payload: BatchBody | None = None,
    asset_type: str | None = None,
    db: Session = Depends(get_db),
):
    """批量生成资产封面：为项目下指定类型（可选）、尚未生成封面的资产逐个派发封面生成任务。"""
    from app.models.asset import AssetType

    try:
        atype = AssetType(asset_type) if asset_type else None
        task, total, dispatched_ids, _ = batch_service.batch_asset_covers(db, project_id, atype, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return BatchResp(task=task, total=total, dispatched_ids=dispatched_ids)
