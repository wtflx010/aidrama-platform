"""AI 视频草稿（Video Lab，独立功能）API：创建/列表/更新/删除/文件上传/LLM优化/生成。

2026-08-11 独立化：AI 视频为顶层导航独立功能（不依赖项目）——提供全局端点
（/video-drafts）；保留项目级端点（/projects/{pid}/video-drafts）做兼容，
前端已全部切换到全局端点。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Project
from app.schemas.video_draft import (
    VideoDraftCreate,
    VideoDraftEnhanceBody,
    VideoDraftEnhanceOut,
    VideoDraftFileUpload,
    VideoDraftGenerateResp,
    VideoDraftOut,
    VideoDraftRegenResp,
    VideoDraftUpdate,
    VideoDraftUploadResp,
)
from app.services import video_draft_service

router = APIRouter()


# ─── 全局端点（AI 视频独立功能，2026-08-11）────────────────────────

@router.post("/video-drafts", response_model=VideoDraftOut, status_code=201)
def create_draft_global(payload: VideoDraftCreate, db: Session = Depends(get_db)):
    """创建视频草稿（独立模式，project_id 可选，仅引用资产时关联项目）。"""
    if payload.project_id and not db.get(Project, payload.project_id):
        raise HTTPException(404, "关联项目不存在")
    try:
        return video_draft_service.create(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/video-drafts", response_model=list[VideoDraftOut])
def list_drafts_global(db: Session = Depends(get_db)):
    """全局草稿列表（不依赖项目）。"""
    return video_draft_service.list_all(db)


@router.post("/video-drafts/upload", response_model=VideoDraftUploadResp, status_code=201)
def upload_draft_file_global(
    payload: VideoDraftFileUpload, db: Session = Depends(get_db)
):
    """上传参考素材文件（图片或视频，base64），返回本地静态 URL 供草稿引用。"""
    try:
        url = video_draft_service.upload_file(db, None, payload.filename, payload.data_base64)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@router.post("/video-drafts/enhance", response_model=VideoDraftEnhanceOut)
def enhance_draft_global(
    body: VideoDraftEnhanceBody, db: Session = Depends(get_db)
):
    """LLM 优化提示词（含参考标签 + 负面词联动）。不依赖草稿，建草稿前可预览。"""
    try:
        prompt, negative = video_draft_service.enhance(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"enhanced_prompt": prompt, "negative_prompt": negative}


@router.get("/video-drafts/{draft_id}", response_model=VideoDraftOut)
def get_draft(draft_id: UUID, db: Session = Depends(get_db)):
    draft = video_draft_service.get(db, draft_id)
    if not draft:
        raise HTTPException(404, "视频草稿不存在")
    return draft


@router.put("/video-drafts/{draft_id}", response_model=VideoDraftOut)
def update_draft(draft_id: UUID, payload: VideoDraftUpdate, db: Session = Depends(get_db)):
    try:
        return video_draft_service.update(db, draft_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/video-drafts/{draft_id}")
def delete_draft(draft_id: UUID, db: Session = Depends(get_db)):
    if not video_draft_service.delete(db, draft_id):
        raise HTTPException(404, "视频草稿不存在")
    return {"ok": True}


@router.post(
    "/video-drafts/{draft_id}/generate",
    response_model=VideoDraftGenerateResp,
    status_code=201,
)
def generate_draft(draft_id: UUID, db: Session = Depends(get_db)):
    """提交视频生成任务（按参考素材自动路由 R2V / FL2VA）。"""
    try:
        task = video_draft_service.generate(db, draft_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"task_id": task.id}


@router.post(
    "/video-drafts/{draft_id}/regenerate",
    response_model=VideoDraftRegenResp,
    status_code=201,
)
def regenerate_draft(draft_id: UUID, db: Session = Depends(get_db)):
    """二采（超分方案）：为成功草稿创建 1080p 二采行并派发超分任务（返回新草稿+任务）。"""
    try:
        new_draft, task = video_draft_service.regenerate(db, draft_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"draft_id": new_draft.id, "task_id": task.id}


# ─── 项目级端点（兼容保留，前端已切全局端点）────────────────────────

@router.post("/projects/{project_id}/video-drafts", response_model=VideoDraftOut, status_code=201)
def create_draft(project_id: UUID, payload: VideoDraftCreate, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    payload.project_id = project_id
    try:
        return video_draft_service.create(db, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{project_id}/video-drafts", response_model=list[VideoDraftOut])
def list_drafts(project_id: UUID, db: Session = Depends(get_db)):
    return video_draft_service.list_by_project(db, project_id)


@router.post(
    "/projects/{project_id}/video-drafts/upload",
    response_model=VideoDraftUploadResp,
    status_code=201,
)
def upload_draft_file(
    project_id: UUID, payload: VideoDraftFileUpload, db: Session = Depends(get_db)
):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    try:
        url = video_draft_service.upload_file(
            db, project_id, payload.filename, payload.data_base64,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@router.post(
    "/projects/{project_id}/video-drafts/enhance",
    response_model=VideoDraftEnhanceOut,
)
def enhance_draft(
    project_id: UUID, body: VideoDraftEnhanceBody, db: Session = Depends(get_db)
):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    body.project_id = project_id
    try:
        prompt, negative = video_draft_service.enhance(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"enhanced_prompt": prompt, "negative_prompt": negative}
