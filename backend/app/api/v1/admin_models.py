from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.model_config import (
    ModelCreate,
    ModelOut,
    ModelTestResult,
    ModelUpdate,
    SaveApiKeyBody,
    SetDefaultBody,
    ToggleBody,
)
from app.services import model_service

router = APIRouter()


@router.post("/keys", response_model=dict)
def save_api_key(body: SaveApiKeyBody):
    """把第三方 API Key 写入 backend/.env（覆盖 or 追加，保持其他行不变）。

    安全约束：
    - env_name 必须以 _KEY 或 _TOKEN 结尾，且仅含大小写字母/数字/下划线
    - api_key 非空且不含换行
    写入后本进程内即通过 os.environ 生效（无需重启）。"""
    import os
    import re
    from pathlib import Path

    env = (body.env_name or "").strip()
    key = (body.api_key or "").strip()
    if not env or not re.fullmatch(r"[A-Za-z0-9_]+", env):
        raise HTTPException(400, "环境变量名只能包含字母/数字/下划线")
    if not (env.endswith("_KEY") or env.endswith("_TOKEN")):
        raise HTTPException(400, "环境变量名必须以 _KEY 或 _TOKEN 结尾（安全限制）")
    if not key:
        raise HTTPException(400, "API Key 不能为空")
    if "\n" in key or "\r" in key:
        raise HTTPException(400, "API Key 不能包含换行")

    env_file = Path(__file__).resolve().parents[3] / ".env"  # backend/.env
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"读取 .env 失败: {e}")
    kept = [ln for ln in lines if not ln.lstrip().startswith(env + "=")]
    kept.append(f"{env}={key}")
    try:
        env_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"写入 .env 失败: {e}")
    os.environ[env] = key
    return {"ok": True, "env_name": env}


@router.get("", response_model=list[ModelOut])
def list_all(db: Session = Depends(get_db)):
    return model_service.list_models(db)


@router.post("", response_model=ModelOut, status_code=201)
def create(payload: ModelCreate, db: Session = Depends(get_db)):
    return model_service.create(db, payload)


@router.get("/{model_id}", response_model=ModelOut)
def get_one(model_id: UUID, db: Session = Depends(get_db)):
    m = model_service.get(db, model_id)
    if not m:
        raise HTTPException(404, "模型不存在")
    return m


@router.put("/{model_id}", response_model=ModelOut)
def update_one(model_id: UUID, payload: ModelUpdate, db: Session = Depends(get_db)):
    m = model_service.update(db, model_id, payload)
    if not m:
        raise HTTPException(404, "模型不存在")
    return m


@router.post("/{model_id}/toggle", response_model=ModelOut)
def toggle(model_id: UUID, body: ToggleBody, db: Session = Depends(get_db)):
    m = model_service.toggle(db, model_id, body.is_enabled)
    if not m:
        raise HTTPException(404, "模型不存在")
    return m


@router.post("/{model_id}/test", response_model=ModelTestResult)
def test(model_id: UUID, db: Session = Depends(get_db)):
    ok, msg = model_service.test_connection(db, model_id)
    return ModelTestResult(ok=ok, message=msg)


@router.post("/{model_id}/set-default", response_model=ModelOut)
def set_default(model_id: UUID, body: SetDefaultBody, db: Session = Depends(get_db)):
    m = model_service.set_default(db, model_id)
    if not m:
        raise HTTPException(404, "模型不存在")
    return m


@router.delete("/{model_id}")
def delete(model_id: UUID, db: Session = Depends(get_db)):
    if not model_service.delete(db, model_id):
        raise HTTPException(404, "模型不存在")
    return {"ok": True}
