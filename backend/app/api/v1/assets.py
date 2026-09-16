"""资产 API：角色/场景/道具 CRUD + 封面/四视图/场景图生成 + 描述扩写 + 角色声线档案。

资产跟项目（不做全局复用）。所有资产必须关联到某个项目。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.asset import Asset, AssetType
from app.models.project import Project
from app.schemas.asset import (
    AssetCreate,
    AssetGenerateResp,
    AssetOut,
    AssetUpdate,
    ExpandDescBody,
    ExpandDescOut,
    NarratorProfileOut,
    NarratorProfileUpdate,
    ProjectBindBody,
    RecommendVoiceResp,
    VoiceProfileUpdate,
)
from app.services import asset_service, character_voice_service

router = APIRouter()


class AssetBatchDeleteBody(BaseModel):
    """批量删除资产请求体。"""
    asset_ids: list[UUID]


class RecommendVoiceBody(BaseModel):
    model_id: UUID | None = None


def _out(db: Session, asset) -> AssetOut:
    """填充资产绑定的项目信息（源自归属 + project_asset 表）。"""
    data = AssetOut.model_validate(asset)
    data.project_ids = [
        UUID(p) for p in asset_service.project_ids_of(db, asset.id)
    ]
    return data


# ─── 项目级资产端点 ─────────────────────────────────────────────────

@router.get("/projects/{project_id}/assets", response_model=list[AssetOut])
def list_assets(project_id: UUID, type: AssetType | None = None, db: Session = Depends(get_db)):
    return [_out(db, a) for a in asset_service.list_by_project(db, project_id, type)]


@router.get("/assets/library", response_model=list[AssetOut])
def list_library(type: AssetType | None = None, db: Session = Depends(get_db)):
    """全局资产库（含全局 + 各项目归属资产）。"""
    return [_out(db, a) for a in asset_service.list_library(db, type)]


@router.get("/assets/{asset_id}", response_model=AssetOut)
def get_asset_detail(asset_id: UUID, db: Session = Depends(get_db)):
    """资产详情（资产详情页）：单条资产全字段。"""
    asset = asset_service.get(db, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    return _out(db, asset)


@router.post("/assets/batch-delete", response_model=dict)
def batch_delete_assets(body: AssetBatchDeleteBody, db: Session = Depends(get_db)):
    """批量删除资产（含关联文件）。

    2026-08-23 绑定保护：已绑定项目的资产不允许删除（跳过并返回 protected 列表）。
    """
    from sqlalchemy import select as _select

    ids = body.asset_ids or []
    existing = set(
        db.scalars(_select(Asset.id).where(Asset.id.in_(ids))).all()
    )
    deleted = 0
    protected = []
    for aid in ids:
        if aid not in existing:
            continue
        try:
            if asset_service.delete(db, aid):
                deleted += 1
        except ValueError:
            protected.append(str(aid))
    return {"ok": True, "deleted": deleted, "protected": protected}


@router.post("/assets/delete-all", response_model=dict)
def delete_all_assets(db: Session = Depends(get_db)):
    """一键删除全部资产（含关联文件）。

    2026-08-23 绑定保护：已绑定项目的资产不允许删除（跳过并返回 protected 列表），
    仅删除未绑定任何项目的纯全局资产。
    """
    from sqlalchemy import select as _select

    ids = list(db.scalars(_select(Asset.id)).all())
    deleted = 0
    protected = []
    for aid in ids:
        try:
            if asset_service.delete(db, aid):
                deleted += 1
        except ValueError:
            protected.append(str(aid))
    return {"ok": True, "deleted": deleted, "protected": protected}


@router.post("/assets/{asset_id}/bind", response_model=dict)
def bind_asset(asset_id: UUID, body: ProjectBindBody, db: Session = Depends(get_db)):
    """把资产绑定到指定项目（全局库 → 项目可用；项目删除时解绑保留）。"""
    # 方案A：风格一致性校验——资产带风格指纹且与目标项目风格不符时拒绝绑定，
    # 防止把 A 项目画风的资产串到 B 项目（无指纹的旧资产放行）。
    try:
        asset = db.get(Asset, asset_id)
        project = db.get(Project, body.project_id)
        if (asset is not None and project is not None
                and asset_service.asset_style_matches(db, asset, project) is False):
            raise ValueError(
                "资产风格指纹与目标项目不一致（方案A），请勿直接绑定；如需复用请在目标项目内重新生成该资产"
            )
        asset_service.bind_asset(db, asset_id, body.project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/assets/{asset_id}/unbind", response_model=dict)
def unbind_asset(asset_id: UUID, body: ProjectBindBody, db: Session = Depends(get_db)):
    """解除资产与项目的绑定（手动解绑或项目删除）。"""
    try:
        asset_service.unbind_asset(db, asset_id, body.project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/projects/{project_id}/assets", response_model=AssetOut, status_code=201)
def create_asset(project_id: UUID, payload: AssetCreate, db: Session = Depends(get_db)):
    try:
        return _out(db, asset_service.create(db, project_id, payload))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/assets/{asset_id}", response_model=AssetOut)
def update_asset(asset_id: UUID, payload: AssetUpdate, db: Session = Depends(get_db)):
    try:
        return asset_service.update(db, asset_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: UUID, db: Session = Depends(get_db)):
    """删除单个资产。已绑定项目的资产返回 400（不允许删除）。"""
    try:
        deleted = asset_service.delete(db, asset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not deleted:
        raise HTTPException(404, "资产不存在")
    return {"ok": True}


class AssetImageUploadBody(BaseModel):
    """base64 图片上传（避免 python-multipart 依赖），角色/场景/道具通用。"""
    filename: str
    data_base64: str  # 不带 data: 前缀的纯 base64


@router.post("/assets/{asset_id}/upload-image", response_model=AssetOut, status_code=200)
def upload_asset_image(asset_id: UUID, payload: AssetImageUploadBody, db: Session = Depends(get_db)):
    """人工上传图片作为资产封面/主图（角色/场景/道具通用）。

    覆盖 cover_url 并置资产状态为 succeeded（等效于"用人工图替代 AI 生成封面"）。
    """
    try:
        asset = asset_service.upload_cover_image(
            db, asset_id, payload.filename, payload.data_base64,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return asset


@router.post("/assets/{asset_id}/generate-cover", response_model=AssetGenerateResp, status_code=201)
def generate_cover(asset_id: UUID, db: Session = Depends(get_db)):
    try:
        asset, task = asset_service.generate_cover(db, asset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return AssetGenerateResp(asset=asset, task=task)


@router.post("/assets/{asset_id}/generate-fourview", response_model=AssetGenerateResp, status_code=201)
def generate_fourview(asset_id: UUID, db: Session = Depends(get_db)):
    try:
        asset, task = asset_service.generate_fourview(db, asset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return AssetGenerateResp(asset=asset, task=task)


@router.post("/assets/{asset_id}/generate-image", response_model=AssetGenerateResp, status_code=201)
def generate_image(asset_id: UUID, db: Session = Depends(get_db)):
    """场景/道具图生成（按资产 type 自动选 scene_code）。"""
    try:
        asset = asset_service.get(db, asset_id)
        if not asset:
            raise ValueError("资产不存在")
        if asset.type == AssetType.scene:
            asset, task = asset_service.generate_scene_image(db, asset_id)
        elif asset.type == AssetType.prop:
            asset, task = asset_service.generate_prop_image(db, asset_id)
        else:
            asset, task = asset_service.generate_cover(db, asset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return AssetGenerateResp(asset=asset, task=task)


class SceneMultiviewBody(BaseModel):
    """场景多视角生成可选覆盖机位组（POV 六格渲染格式）。"""
    shots: list | None = None  # [{"name","view_text"}, ...]；空/缺省则用已存/默认/导演推断


@router.post("/assets/{asset_id}/generate-scene-multiview", response_model=AssetGenerateResp, status_code=201)
def generate_scene_multiview(asset_id: UUID, payload: SceneMultiviewBody | None = None,
                             db: Session = Depends(get_db)):
    """场景多视角生成（POV 机位六格合一图，2026-08-18 v5：默认/导演推断/GUI覆盖 三层机位）。"""
    try:
        asset, task = asset_service.generate_scene_multiview(db, asset_id, shots=payload.shots if payload else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return AssetGenerateResp(asset=asset, task=task)


@router.post("/assets/{asset_id}/expand-description", response_model=ExpandDescOut)
def expand_description(asset_id: UUID, payload: ExpandDescBody, db: Session = Depends(get_db)):
    try:
        expanded = asset_service.expand_description(db, asset_id, payload.model_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return ExpandDescOut(expanded_description=expanded)


# ─── 角色声线档案（P2 差异化配音）─────────────────────────────────

@router.post("/assets/{asset_id}/recommend-voice", response_model=RecommendVoiceResp)
def recommend_voice(asset_id: UUID, payload: RecommendVoiceBody | None = None, db: Session = Depends(get_db)):
    """LLM 根据角色设定推荐声线档案（gender/age_group/timbre_tags/default_emotion/voice_description）。"""
    try:
        model_id = payload.model_id if payload else None
        profile = character_voice_service.recommend_voice_profile(db, asset_id, model_id)
        asset, warnings = character_voice_service.apply_voice_profile(db, asset_id, profile)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RecommendVoiceResp(
        asset_id=asset.id, voice_profile=asset.voice_profile, warnings=warnings,
    )


@router.put("/assets/{asset_id}/voice-profile", response_model=AssetOut)
def update_voice_profile(asset_id: UUID, payload: VoiceProfileUpdate,
                         db: Session = Depends(get_db),
                         response: Response = None):
    """更新角色声线档案（部分更新，合并非覆盖）。

    声线-视觉一致性警告通过响应头 X-Voice-Warnings 返回（JSON 数组，不阻断写入）。
    """
    import json as _json
    try:
        partial = payload.model_dump(exclude_none=True)
        asset, warnings = character_voice_service.update_voice_profile(db, asset_id, partial)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if warnings:
        response.headers["X-Voice-Warnings"] = _json.dumps(warnings, ensure_ascii=False)
    return asset


# ─── 项目旁白声线（P2）────────────────────────────────────────────

@router.get("/projects/{project_id}/narrator-profile", response_model=NarratorProfileOut)
def get_narrator_profile(project_id: UUID, db: Session = Depends(get_db)):
    """获取项目旁白声线（未配置返回默认中性男声）。"""
    profile = character_voice_service.get_narrator_profile(db, project_id)
    return NarratorProfileOut(narrator_profile=profile)


@router.put("/projects/{project_id}/narrator-profile", response_model=NarratorProfileOut)
def update_narrator_profile(project_id: UUID, payload: NarratorProfileUpdate, db: Session = Depends(get_db)):
    """更新项目旁白声线。"""
    try:
        partial = payload.model_dump(exclude_none=True)
        project = character_voice_service.set_narrator_profile(
            db, project_id, character_voice_service.get_narrator_profile(db, project_id) | partial
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return NarratorProfileOut(narrator_profile=project.narrator_profile)
