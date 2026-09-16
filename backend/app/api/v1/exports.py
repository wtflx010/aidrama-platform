"""导出 API：发起成片导出 / 列出导出任务。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.task import EpisodeExportOut, TaskOut
from app.services import export_service

router = APIRouter()


# 注意：导出端点直接返回 TaskOut（id 在顶层），不像 keyframe/video 生成端点
# 返回 GenerateResp（{"task": {...}} 包装）。前端 ExportPanel.tsx 已适配此差异。
@router.post("/projects/{project_id}/export", response_model=TaskOut, status_code=201)
def export(
    project_id: UUID,
    # 默认关配音：视频生成模型（Agnes）原生输出角色语音/旁白，导出直接用视频自带
    # 音轨即可，无需 TTS 配音混入。TTS 链路保留，需要时传 include_voice=true 启用。
    include_voice: bool = False,
    include_subtitle: bool = True,
    burn_subtitle: bool = True,
    include_bgm: bool = True,
    include_sfx: bool = True,
    db: Session = Depends(get_db),
):
    try:
        return export_service.export(
            db, project_id,
            include_voice=include_voice,
            include_subtitle=include_subtitle,
            burn_subtitle=burn_subtitle,
            include_bgm=include_bgm,
            include_sfx=include_sfx,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{project_id}/exports", response_model=list[TaskOut])
def list_exports(project_id: UUID, db: Session = Depends(get_db)):
    return export_service.list_by_project(db, project_id)


@router.post("/episodes/{episode_id}/export", response_model=TaskOut, status_code=201)
def export_episode(
    episode_id: UUID,
    # 默认关配音：视频生成模型（Agnes）原生输出角色语音/旁白，导出直接用视频自带
    # 音轨即可，无需 TTS 配音混入。
    include_voice: bool = False,
    include_subtitle: bool = True,
    burn_subtitle: bool = True,
    include_bgm: bool = True,
    include_sfx: bool = True,
    db: Session = Depends(get_db),
):
    """剧集导出：按幕导出单集成片（覆盖同幕旧成片）。"""
    try:
        return export_service.export_episode(
            db, episode_id,
            include_voice=include_voice,
            include_subtitle=include_subtitle,
            burn_subtitle=burn_subtitle,
            include_bgm=include_bgm,
            include_sfx=include_sfx,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/episodes/{episode_id}/export", response_model=EpisodeExportOut)
def get_episode_export(episode_id: UUID, db: Session = Depends(get_db)):
    """该幕最新剧集导出任务 + 可导出性判断（供前端展示准备状态）。"""
    return export_service.get_episode_export(db, episode_id)


@router.delete("/exports/{task_id}", status_code=204)
def delete_export(task_id: UUID, db: Session = Depends(get_db)):
    """删除成片：删除磁盘上的 film.mp4 + 导出任务行。"""
    if not export_service.delete(db, task_id):
        raise HTTPException(404, "导出任务不存在")
    return None
