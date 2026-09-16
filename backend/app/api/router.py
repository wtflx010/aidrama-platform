from fastapi import APIRouter, Depends

from app.deps import require_auth
from app.api.v1 import (
    admin_models,
    agent,
    art_styles,
    canvas,
    assets,
    batch,
    bgm,
    episodes,
    evaluate,
    exports,
    keyframes,
    models,
    multilingual,
    novels,
    projects,
    rework,
    segments,
    sfx,
    tasks,
    video_drafts,
    videos,
    voice,
)

# 全局可选鉴权：配置 API_AUTH_TOKEN 后所有 /api 路由（health 除外）需携带 token。
# health 在 main.py 单独挂载，保持健康检查免鉴权。
api_router = APIRouter(dependencies=[Depends(require_auth)])
api_router.include_router(models.router, prefix="/models", tags=["模型(用户侧)"])
api_router.include_router(canvas.router, prefix="/canvas", tags=["画布(生图工作台)"])
api_router.include_router(admin_models.router, prefix="/admin/models", tags=["模型(管理)"])
api_router.include_router(art_styles.router, prefix="/art-styles", tags=["美术风格"])
api_router.include_router(projects.router, prefix="/projects", tags=["项目"])
api_router.include_router(episodes.router, tags=["幕"])
api_router.include_router(evaluate.router, tags=["成片评估"])
api_router.include_router(segments.router, tags=["分镜"])
api_router.include_router(keyframes.router, tags=["关键帧"])
api_router.include_router(rework.router, tags=["修片工作流"])
api_router.include_router(videos.router, tags=["视频"])
api_router.include_router(assets.router, tags=["美术资产"])
api_router.include_router(voice.router, tags=["配音字幕"])
api_router.include_router(multilingual.router, tags=["多语言配音字幕"])
api_router.include_router(batch.router, tags=["批量生成"])
api_router.include_router(exports.router, tags=["导出"])
api_router.include_router(bgm.router, tags=["BGM 背景音乐"])
api_router.include_router(sfx.router, tags=["SFX 音效"])
api_router.include_router(tasks.router, tags=["任务"])
api_router.include_router(novels.router, tags=["剧本库"])
api_router.include_router(video_drafts.router, tags=["AI 视频草稿"])
api_router.include_router(agent.router, tags=["创作助手"])
