"""模型配置中心业务服务：CRUD + 启停 + 设默认 + 连接测试。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.model_config import Model
from app.providers.registry import ProviderRegistry
from app.schemas.model_config import ModelCreate, ModelUpdate


def list_models(db: Session, model_type: str | None = None, scene_code: str | None = None,
                enabled_only: bool = False):
    q = select(Model)
    if enabled_only:
        q = q.where(Model.is_enabled.is_(True))
    if model_type:
        q = q.where(Model.model_type == model_type)
    if scene_code:
        # JSONB array contains：scene_codes @> '["scene_code"]'
        q = q.where(Model.scene_codes.contains([scene_code]))
    q = q.order_by(Model.sort.asc(), Model.created_at.asc())
    return db.scalars(q).all()


def get(db: Session, model_id) -> Model | None:
    return db.get(Model, model_id)


def create(db: Session, payload: ModelCreate) -> Model:
    m = Model(**payload.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _clear_conflicting_defaults(db: Session, m: Model) -> None:
    """按场景粒度清理同类型其它模型的默认标志。

    以 scene_codes 交集为准：只有与 m 存在场景交集的模型才需要让位，
    允许 keyframe(文生图) 与 character_fourview(图生图) 等不同场景各自持有默认。
    """
    if m.scene_codes:
        targets = [
            x for x in db.query(Model).filter(
                Model.model_type == m.model_type, Model.id != m.id
            ).all()
            if set(x.scene_codes or []) & set(m.scene_codes)
        ]
        for x in targets:
            x.is_default = False
    else:
        # 模型未配置场景时退回按 model_type 全清（旧行为）
        db.query(Model).filter(
            Model.model_type == m.model_type, Model.id != m.id
        ).update({Model.is_default: False}, synchronize_session=False)


def update(db: Session, model_id, payload: ModelUpdate) -> Model | None:
    m = db.get(Model, model_id)
    if not m:
        return None
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "api_key_ref" and not v:
            continue  # 留空不改
        setattr(m, k, v)
    # 通过编辑表单直写 is_default=True 时，与 set_default 保持一致：
    # 按场景粒度清零，保证同一场景唯一默认（否则 _resolve_model 取值不确定）
    if data.get("is_default"):
        _clear_conflicting_defaults(db, m)
        m.is_enabled = True  # 勾选默认即生效：默认模型必须可被实际使用（2026-08-07）
    db.commit()
    db.refresh(m)
    return m


def toggle(db: Session, model_id, is_enabled: bool) -> Model | None:
    m = db.get(Model, model_id)
    if not m:
        return None
    m.is_enabled = is_enabled
    db.commit()
    db.refresh(m)
    return m


def set_default(db: Session, model_id) -> Model | None:
    """设为默认：按场景粒度清零，保证同一场景唯一默认（不同场景可各有默认）。

    2026-08-07：勾选默认即生效——设默认同时自动启用，确保「默认且生效」的模型
    一定可被实际使用（_resolve_model 只认 is_default+is_enabled 同时为真的模型）。
    """
    m = db.get(Model, model_id)
    if not m:
        return None
    _clear_conflicting_defaults(db, m)
    m.is_default = True
    m.is_enabled = True
    db.commit()
    db.refresh(m)
    return m


def test_connection(db: Session, model_id) -> tuple[bool, str]:
    m = db.get(Model, model_id)
    if not m:
        return False, "模型不存在"
    provider = ProviderRegistry.for_model(m)
    return provider.test_connection()


def delete(db: Session, model_id) -> bool:
    """删除模型配置：先把引用该模型的记录 model_id 置 NULL，再删除。

    6 张表引用 model.id（task/keyframe/video_clip/voice_line/asset/episode_video）。
    数据库外键已加 ondelete=SET NULL，但应用层先清理可兼容旧约束、且错误信息更友好。
    """
    m = db.get(Model, model_id)
    if not m:
        return False
    from sqlalchemy import text
    for tbl in ("task", "keyframe", "video_clip", "voice_line", "asset", "episode_video"):
        db.execute(text(f"UPDATE {tbl} SET model_id = NULL WHERE model_id = :mid"), {"mid": model_id})
    db.delete(m)
    db.commit()
    return True
