import logging
import os
import pathlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="AI 短剧制作平台", version="0.1.0")

# CORS：默认只允许本地开发源；生产通过 CORS_ORIGINS（逗号分隔）显式配置前端域名。
# 不再使用 allow_origins=["*"] + allow_credentials=True 的不安全组合。
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):  # noqa: ARG001
    return JSONResponse(status_code=400, content={"code": "bad_request", "message": str(exc)})


# 静态资源：生成产物（关键帧图、视频、导出片）
os.makedirs(settings.media_dir, exist_ok=True)
os.makedirs(settings.export_dir, exist_ok=True)
app.mount("/static/media", StaticFiles(directory=settings.media_dir), name="media")
app.mount("/static/exports", StaticFiles(directory=settings.export_dir), name="exports")

app.include_router(api_router, prefix="/api")

# health 免鉴权（健康检查）：单独挂载，不受 api_router 的全局 require_auth 约束
from app.api.v1 import health  # noqa: E402

app.include_router(health.router, prefix="/api", tags=["health"])



@app.on_event("startup")
def _startup_register_preset_voices():
    """启动时注册预置声音到 CosyVoice wrapper（幂等，wrapper 离线则跳过）。"""
    try:
        from app.services.voice_preset_service import register_preset_voices
        register_preset_voices()
    except Exception as e:
        logger.warning("预置声音注册失败（不影响启动）: %s", e)


# ─── 前端托管（已于 2026-08-16 移除）────────────────────────────
# 前端 Web 端口回归 5173（vite dev，双栈监听 + 代理 127.0.0.1:8000），
# 8000 仅提供 API。此前挂在 8000 的 dist 托管已撤销：
#   from fastapi.responses import FileResponse
#   _DIST_DIR = pathlib.Path(...) / "frontend" / "dist"
#   若未来需要 8000 单端口托管，可恢复下方逻辑（含 SPA 深链回退 + 缓存策略）。

# 说明：此处不再挂载前端；4000/8000 均只处理 /api、/gen、/static/media、/static/exports。

