"""剧本库 API：上传导入 / 分析 / 生成项目 / 章节续接追加。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.novel import Novel, NovelAnalysisStatus
from app.models.project import Project
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.novel import (
    AdaptBody,
    AdaptContinuationBody,
    AnalyzeBody,
    AppendChaptersBody,
    ChapterOut,
    NovelDetail,
    NovelOut,
    NovelPosterUpload,
    NovelUpload,
)
from app.services.novel_analysis_service import split_chapters
from app.tasks.generate_novel import (
    adapt_continuation_task,
    adapt_script_task,
    analyze_novel_task,
)

router = APIRouter()


@router.post("/novels/upload", response_model=NovelOut, status_code=201)
def upload_novel(payload: NovelUpload, db: Session = Depends(get_db)):
    """导入剧本文本，创建剧本文档记录（待分析）。"""
    if not payload.text or not payload.text.strip():
        raise HTTPException(400, "剧本内容不能为空")
    novel = Novel(title=payload.title[:200], raw_text=payload.text)
    db.add(novel)
    db.commit()
    db.refresh(novel)
    return novel


@router.get("/novels", response_model=list[NovelOut])
def list_novels(db: Session = Depends(get_db)):
    return db.scalars(select(Novel).order_by(Novel.created_at.desc())).all()


@router.get("/novels/{novel_id}", response_model=NovelDetail)
def get_novel(novel_id: UUID, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    return novel


@router.post("/novels/{novel_id}/analyze", status_code=201)
def trigger_analyze(novel_id: UUID, payload: AnalyzeBody, db: Session = Depends(get_db)):
    """触发剧本分析（异步 Celery 任务）。"""
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    if novel.analysis_status == NovelAnalysisStatus.analyzing:
        raise HTTPException(400, "剧本正在分析中")

    task = Task(
        type=TaskType.analyze_novel,
        target_type="novel",
        target_id=novel_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    analyze_novel_task.delay(str(task.id), str(novel_id), str(payload.model_id) if payload.model_id else None)
    return {"task_id": str(task.id), "novel_id": str(novel_id)}


@router.post("/novels/{novel_id}/upload-image", response_model=NovelOut, status_code=200)
def upload_novel_poster(novel_id: UUID, payload: NovelPosterUpload, db: Session = Depends(get_db)):
    """智能体生成的剧本封面保存为该剧本海报（回写 novel.poster_url）。

    与资产封面人工上传同模式：base64 直传，落盘 {media_dir}/novels/{novel_id}/，
    覆盖 poster_url 并置为剧本海报（原「保存到项目」改造而来，2026-08-24）。
    """
    import base64 as _b64
    import os as _os
    import re as _re
    from pathlib import Path as _Path

    from app.config import settings

    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")

    safe_name = _re.sub(r"[^\w.\-]", "_", payload.filename or "poster.png")
    ext = _Path(safe_name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, f"不支持的图片格式: {ext or '(无扩展名)'}（仅支持 png/jpg/jpeg/webp）")

    try:
        img_bytes = _b64.b64decode(payload.data_base64)
    except Exception:
        raise HTTPException(400, "base64 解码失败")
    if not img_bytes:
        raise HTTPException(400, "图片数据为空")
    if len(img_bytes) > 20 * 1024 * 1024:
        raise HTTPException(400, "图片文件过大（最大 20MB）")

    save_dir = _Path(settings.media_dir) / "novels" / str(novel.id)
    save_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"poster_{_os.urandom(8).hex()}{ext}"
    (save_dir / unique_name).write_bytes(img_bytes)

    novel.poster_url = f"{settings.static_base_url}/media/novels/{novel.id}/{unique_name}"
    db.commit()
    db.refresh(novel)
    return novel


@router.post("/novels/{novel_id}/poster/regenerate", status_code=201)
def regenerate_novel_poster(novel_id: UUID, db: Session = Depends(get_db)):
    """重新生成剧本海报（强制覆盖旧海报，异步 Celery 任务）。

    2026-08-27：封面要求贴合剧本 + 中文准确，旧海报不满意时点此重来。
    """
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    if not (novel.raw_text or "").strip():
        raise HTTPException(400, "剧本内容为空，无法生成海报")
    from app.tasks.generate_novel_poster import generate_novel_poster_task

    task = Task(
        type=TaskType.generate_asset_cover,
        target_type="novel",
        target_id=novel_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    generate_novel_poster_task.delay(str(novel_id), model_id=None, force=True)
    return {"task_id": str(task.id)}


@router.get("/novels/{novel_id}/progress")
def novel_write_progress(novel_id: UUID, db: Session = Depends(get_db)):
    """智能体前端「写剧本完整进度」：剧本写作 / 分镜预览 / 剧本海报 三段最近任务。"""
    from sqlalchemy import select

    from app.models.task import Task as _Task, TaskType as _TT
    from app.schemas.task import TaskOut as _TaskOut

    def _last(ttype, extra=None):
        q = (
            select(_Task)
            .where(
                _Task.target_type == "novel",
                _Task.target_id == novel_id,
                _Task.type == ttype,
            )
        )
        if extra is not None:
            q = q.where(extra)
        return db.scalars(q.order_by(_Task.created_at.desc(), _Task.id.desc()).limit(1)).first()

    def _out(t):
        return _TaskOut.model_validate(t).model_dump(mode="json") if t else None

    return {
        "script_task": _out(_last(_TT.write_script)),
        "shot_task": _out(_last(_TT.adapt_script, _Task.provider_task_id == "shot_plan")),
        "poster_task": _out(_last(_TT.generate_asset_cover)),
    }


@router.post("/novels/{novel_id}/adapt", status_code=201)
def trigger_adapt(novel_id: UUID, payload: AdaptBody, db: Session = Depends(get_db)):
    """触发生成项目（异步 Celery 任务）。

    剧本是结构化产物，直接读取剧本文本改编为项目（跳过小说分析步骤）；
    若剧本已完成分析（如由小说改编而来）则沿用分析结果改编。
    """
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    if not (novel.raw_text or "").strip():
        raise HTTPException(400, "剧本内容为空，无法生成项目")

    task = Task(
        type=TaskType.adapt_script,
        target_type="novel",
        target_id=novel_id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    common = dict(
        model_id=str(payload.model_id) if payload.model_id else None,
        per_duration=payload.per_duration,
        style=payload.style,
        style_id=str(payload.style_id) if payload.style_id else None,
        art_style_prompt=payload.art_style_prompt,
        aspect_ratio=payload.aspect_ratio,
        resolution=payload.resolution,
        video_params=payload.video_params,
    )
    if novel.analysis_status == NovelAnalysisStatus.done and novel.analysis_result:
        # 已有分析结果（小说改编产物）：沿用旧路径
        adapt_script_task.delay(str(task.id), str(novel_id), **common)
    else:
        # 剧本直读路径（默认）：跳过分析直接生成项目
        from app.tasks.generate_novel import adapt_script_direct_task
        adapt_script_direct_task.delay(str(task.id), str(novel_id), **common)
    return {"task_id": str(task.id), "novel_id": str(novel_id)}


@router.post("/novels/{novel_id}/shot-plan", status_code=201)
def trigger_shot_plan(novel_id: UUID, db: Session = Depends(get_db)):
    """手工导入剧本「生成分镜」（异步 Celery 任务）。

    剧本文本 → 分镜预览写入 novel.shot_plan（不建项目）：
    - 导入剧本本身已写好分镜（分镜N（X秒）/景别/画面 等确认稿格式）→
      parse_storyboard 直落，**生成的分镜按导入的剧本执行**，不重新 LLM 生成；
    - 否则按 MiniMax H3 分镜规范逐集 LLM 结构化生成。
    已生成项目的剧本分镜以项目为准（在项目工作台调整）。
    """
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    if not (novel.raw_text or "").strip():
        raise HTTPException(400, "剧本内容为空，无法生成分镜")
    if novel.project_id:
        raise HTTPException(400, "剧本已生成项目，分镜以项目为准（可在项目工作台调整）")

    # 防重复触发：已有 pending/running 的分镜任务时直接复用返回
    running = db.scalars(
        select(Task).where(
            Task.type == TaskType.generate_shot_plan,
            Task.target_type == "novel",
            Task.target_id == novel_id,
            Task.status.in_([TaskStatus.pending, TaskStatus.running]),
        ).order_by(Task.created_at.desc(), Task.id.desc()).limit(1)
    ).first()
    if running is not None:
        return {"task_id": str(running.id), "novel_id": str(novel_id), "already_running": True}

    task = Task(
        type=TaskType.generate_shot_plan,
        target_type="novel",
        target_id=novel_id,
        status=TaskStatus.pending,
        result_url=f"/novels/{novel_id}",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from app.tasks.generate_novel import generate_shot_plan_task
    generate_shot_plan_task.delay(str(task.id), str(novel_id))
    return {"task_id": str(task.id), "novel_id": str(novel_id)}


@router.post("/novels/{novel_id}/polish")
def polish_novel(novel_id: UUID, db: Session = Depends(get_db)):
    """AI 润色整体剧本（同步 LLM）：保持剧情/分集结构不变，提升对白与画面感。"""
    from app.services.script_polish_service import polish_script

    try:
        result = polish_script(db, str(novel_id))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.delete("/novels/{novel_id}")
def delete_novel(novel_id: UUID, db: Session = Depends(get_db)):
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    db.delete(novel)
    db.commit()
    return {"ok": True}


@router.get("/novels/{novel_id}/chapters", response_model=list[ChapterOut])
def list_novel_chapters(
    novel_id: UUID,
    project_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    """章节列表（P6）：切分小说章节，标记相对指定项目（或小说默认项目）已追加的断点。"""
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")

    chapters = split_chapters(novel.raw_text)

    # 确定断点项目：优先显式 project_id，其次 novel.project_id（默认项目）
    upto = 0
    project = None
    if project_id is not None:
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(404, "项目不存在")
        if project.source_novel_id not in (None, novel_id) and novel.project_id != project.id:
            raise HTTPException(400, "项目与本小说无关联")
    elif novel.project_id is not None:
        project = db.get(Project, novel.project_id)
    if project is not None:
        upto = project.processed_upto_chapter or 0

    return [
        {"index": c["index"], "title": c["title"], "processed": c["index"] <= upto}
        for c in chapters
    ]


@router.post("/novels/{novel_id}/chapters/append", response_model=list[ChapterOut])
def append_novel_chapters(
    novel_id: UUID,
    payload: AppendChaptersBody,
    db: Session = Depends(get_db),
):
    """追加后续章节（连载更新）：把新章节文本合并进小说 raw_text。

    合并后 split_chapters 重新切分，原有章节/断点保持不变，
    新章节 index 从原总章数 +1 续接，供「追加章节」改编为下一批新幕。
    要求新文本包含章节标记（第X章 / Chapter N 等），否则拒绝合并。
    """
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    if not payload.text or not payload.text.strip():
        raise HTTPException(400, "章节内容不能为空")

    old_total = len(split_chapters(novel.raw_text))
    merged_text = novel.raw_text.rstrip() + "\n\n" + payload.text.strip()
    merged = split_chapters(merged_text)
    if len(merged) <= old_total:
        raise HTTPException(
            400,
            "未识别到新的章节标记，请确保上传内容包含「第X章」「Chapter N」等章节标记",
        )

    novel.raw_text = merged_text
    novel.chapters_count = len(merged)
    novel.word_count = len(merged_text)
    db.commit()

    # 断点标记：基于小说默认项目（与 GET /chapters 无 project_id 时一致）
    upto = 0
    if novel.project_id is not None:
        project = db.get(Project, novel.project_id)
        if project is not None:
            upto = project.processed_upto_chapter or 0
    return [
        {"index": c["index"], "title": c["title"], "processed": c["index"] <= upto}
        for c in merged
    ]


@router.post("/novels/{novel_id}/adapt-continuation", status_code=201)
def trigger_adapt_continuation(
    novel_id: UUID,
    payload: AdaptContinuationBody,
    db: Session = Depends(get_db),
):
    """触发章节续接追加（P6）：把 [chapter_start, chapter_end] 章节改编为新幕，
    续接到已有项目（异步 Celery 任务）。"""
    novel = db.get(Novel, novel_id)
    if not novel:
        raise HTTPException(404, "剧本不存在")
    if novel.analysis_status != NovelAnalysisStatus.done or not novel.analysis_result:
        raise HTTPException(400, "剧本尚未分析完成，请先分析")

    project = db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(404, "目标项目不存在")
    if project.source_novel_id != novel_id:
        raise HTTPException(400, "项目与本小说无关联，无法续接追加")

    # 断点校验：章节必须按序续接（与服务层一致，尽早返回 400）
    upto = project.processed_upto_chapter or 0
    if payload.chapter_start != upto + 1:
        raise HTTPException(
            400,
            f"章节追加必须按序续接：已改编到第 {upto} 章，本批应从第 {upto + 1} 章开始"
            f"（收到 chapter_start={payload.chapter_start}）",
        )
    if payload.chapter_end < payload.chapter_start:
        raise HTTPException(400, "chapter_end 必须 >= chapter_start")

    chapters = split_chapters(novel.raw_text)
    if payload.chapter_end > len(chapters):
        raise HTTPException(
            400, f"小说共 {len(chapters)} 章，chapter_end={payload.chapter_end} 超出范围"
        )

    task = Task(
        type=TaskType.adapt_continuation,
        project_id=project.id,
        target_type="project",
        target_id=project.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    adapt_continuation_task.delay(
        str(task.id),
        str(novel_id),
        str(project.id),
        payload.chapter_start,
        payload.chapter_end,
        str(payload.model_id) if payload.model_id else None,
    )
    return {"task_id": str(task.id), "project_id": str(project.id), "novel_id": str(novel_id)}
