"""画布(生图工作台)API:M1 文档 CRUD + 版本回滚 + 分镜导入 + 批量生成。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.canvas import (
    CanvasBoardCreate,
    CanvasBoardGenerate,
    CanvasBoardGenerateOut,
    CanvasBoardOut,
    CanvasBoardSave,
    CanvasNodeGenerateOut,
    DirectorGenerate,
    DirectorGenerateOut,
    SchemeGenerate,
    SchemeGenerateOut,
    SchemeMeta,
    CanvasSegmentImport,
    CanvasSegmentsImport,
)
from app.services import canvas_service

router = APIRouter()


@router.get("/schemes", response_model=list[SchemeMeta])
def list_schemes():
    """画布生成方案列表：前端方案菜单（2026-08-29）。"""
    from app.services import canvas_schemes

    return [SchemeMeta(**m) for m in canvas_schemes.list_schemes()]


@router.get("", response_model=list[CanvasBoardOut])
def list_boards(db: Session = Depends(get_db)):
    return canvas_service.list_boards(db)


@router.post("", response_model=CanvasBoardOut, status_code=201)
def create(payload: CanvasBoardCreate, db: Session = Depends(get_db)):
    return canvas_service.create(db, payload.name, payload.project_id)


@router.get("/{board_id}", response_model=CanvasBoardOut)
def get(board_id: UUID, db: Session = Depends(get_db)):
    try:
        return canvas_service.get(db, board_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/{board_id}", response_model=CanvasBoardOut)
def save(board_id: UUID, payload: CanvasBoardSave, db: Session = Depends(get_db)):
    try:
        return canvas_service.save(db, board_id, payload.document, payload.name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{board_id}", status_code=204)
def delete(board_id: UUID, db: Session = Depends(get_db)):
    try:
        canvas_service.delete(db, board_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{board_id}/rollback", response_model=CanvasBoardOut)
def rollback(board_id: UUID, db: Session = Depends(get_db)):
    try:
        return canvas_service.rollback(db, board_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/import-segments", response_model=CanvasBoardOut, status_code=201)
def import_segments(payload: CanvasSegmentsImport, db: Session = Depends(get_db)):
    """多分镜合并导入(M3):一次把多个分镜(可跨幕)落成一张画布。"""
    try:
        return canvas_service.import_segments(db, payload.segment_ids, payload.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/import-segment", response_model=CanvasBoardOut, status_code=201)
def import_segment(payload: CanvasSegmentImport, db: Session = Depends(get_db)):
    try:
        return canvas_service.import_from_segment(db, payload.segment_id, payload.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{board_id}/audit")
def audit_board(board_id: UUID, db: Session = Depends(get_db)):
    """画布体检/评审:失败/未绑定/无产物/无点位 + 任务运行状态(纯读)。"""
    try:
        return canvas_service.audit_board(db, board_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{board_id}/generate", response_model=CanvasBoardGenerateOut)
def generate(board_id: UUID, payload: CanvasBoardGenerate, db: Session = Depends(get_db)):
    try:
        task, results = canvas_service.generate(db, board_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return CanvasBoardGenerateOut(
        task_id=task.id,
        nodes=[CanvasNodeGenerateOut(**r) for r in results],
    )

@router.post("/{board_id}/director-generate", response_model=DirectorGenerateOut)
def director_generate(board_id: UUID, payload: DirectorGenerate, db: Session = Depends(get_db)):
    """导演台模式（多段连续生视频，M2+.5）：组装导演台时间轴并派发单个任务。"""
    from app.services import canvas_director_service

    try:
        task, node_ids, total_frames = canvas_director_service.generate(db, board_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return DirectorGenerateOut(
        task_id=task.id, board_id=board_id, node_ids=node_ids,
        message=f"导演台任务已提交（{len(node_ids)} 段, {total_frames} 帧）",
    )

@router.post("/{board_id}/scheme-generate", response_model=SchemeGenerateOut)
def scheme_generate(board_id: UUID, payload: SchemeGenerate, db: Session = Depends(get_db)):
    """统一方案执行入口（2026-08-29）：按 payload.scheme 分发。
    既有的 /generate 与 /director-generate 保留为兼容薄壳。"""
    from app.services import canvas_schemes

    try:
        task, node_ids, extra = canvas_schemes.generate(db, board_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return SchemeGenerateOut(
        task_id=task.id, board_id=board_id, scheme=payload.scheme,
        node_ids=node_ids, nodes=extra,
        message=f"方案「{payload.scheme}」任务已提交",
    )


