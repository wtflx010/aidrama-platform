"""美术风格预设 API：列表 / 详情 / 创建 / 更新 / 删除。

内置预设（is_builtin=True）不可删除，可更新 prompt_fragment 等字段。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.art_style import ArtStyle
from app.schemas.art_style import ArtStyleCreate, ArtStyleOut, ArtStyleUpdate

router = APIRouter()


@router.get("", response_model=list[ArtStyleOut])
def list_art_styles(category: str | None = None, db: Session = Depends(get_db)):
    """风格列表（可选 category 过滤，按 sort_order 排序）。"""
    q = select(ArtStyle)
    if category:
        q = q.where(ArtStyle.category == category)
    q = q.order_by(ArtStyle.sort_order.asc(), ArtStyle.created_at.asc())
    return db.scalars(q).all()


@router.get("/{art_style_id}", response_model=ArtStyleOut)
def get_art_style(art_style_id: UUID, db: Session = Depends(get_db)):
    s = db.get(ArtStyle, art_style_id)
    if not s:
        raise HTTPException(404, "风格不存在")
    return s


@router.post("", response_model=ArtStyleOut, status_code=201)
def create_art_style(payload: ArtStyleCreate, db: Session = Depends(get_db)):
    s = ArtStyle(
        name=payload.name,
        category=payload.category,
        prompt_fragment=payload.prompt_fragment,
        description=payload.description,
        cover_url=payload.cover_url,
        reference_images=payload.reference_images,
        sort_order=payload.sort_order,
        is_builtin=payload.is_builtin,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.put("/{art_style_id}", response_model=ArtStyleOut)
def update_art_style(art_style_id: UUID, payload: ArtStyleUpdate, db: Session = Depends(get_db)):
    s = db.get(ArtStyle, art_style_id)
    if not s:
        raise HTTPException(404, "风格不存在")
    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/{art_style_id}")
def delete_art_style(art_style_id: UUID, db: Session = Depends(get_db)):
    s = db.get(ArtStyle, art_style_id)
    if not s:
        raise HTTPException(404, "风格不存在")
    if s.is_builtin:
        raise HTTPException(400, "内置预设风格不可删除")
    db.delete(s)
    db.commit()
    return {"ok": True}
