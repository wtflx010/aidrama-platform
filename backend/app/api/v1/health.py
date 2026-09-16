from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    result = {"db": "ok", "redis": "ok", "celery": "ok"}
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        result["db"] = "error"
    try:
        import redis as redis_lib

        redis_lib.from_url(settings.redis_url).ping()
    except Exception:
        result["redis"] = "error"
    try:
        from app.tasks.celery_app import celery_app

        celery_app.control.ping(timeout=1)
    except Exception:
        result["celery"] = "error"
    return result
