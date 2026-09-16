"""小说分析与剧本改编 Celery 任务。"""
from app.database import SessionLocal
from app.models.novel import Novel, NovelAnalysisStatus
from app.models.task import Task, TaskStatus
from app.providers.errors import map_to_chinese
from app.tasks.base import heartbeat_guard, now, update_task
from app.tasks.celery_app import celery_app


@celery_app.task(name="analyze_novel", bind=True)
def analyze_novel_task(self, task_id: str, novel_id: str, model_id: str | None = None):
    """小说内容分析：Map-Reduce 摘要 + 情节提取。"""
    from app.services import novel_analysis_service

    db = SessionLocal()
    try:
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=5)
        with heartbeat_guard(task_id, interval=20):
            novel = novel_analysis_service.analyze_novel(db, novel_id, model_id)
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/novels/{novel_id}", finished_at=now(),
        )
    except Exception as e:
        db.rollback()
        # 取消保护：任务已取消时不回写 novel 分析失败状态（取消后应保持 pending 可重新执行）
        t = db.get(Task, task_id)
        if t is not None and t.status == TaskStatus.cancelled:
            return
        novel = db.get(Novel, novel_id)
        if novel:
            novel.analysis_status = NovelAnalysisStatus.failed
            novel.error = str(e)
            db.commit()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()


@celery_app.task(name="generate_shot_plan", bind=True)
def generate_shot_plan_task(
    self, task_id: str, novel_id: str,
    model_id: str | None = None, per_duration: int = 15,
):
    """手工导入剧本「生成分镜」：剧本文本 → 分镜预览，写入 novel.shot_plan（不建项目）。

    复用 build_script_shot_plan 的直落优先逻辑：
    - 导入剧本本身已写好分镜（分镜N（X秒）/景别/画面 等确认稿格式）→
      parse_storyboard 直落，生成的分镜**按导入的剧本执行**，不重新 LLM 生成；
    - 否则按 MiniMax H3 分镜规范逐集 LLM 结构化生成。
    进度/终态由 build_script_shot_plan 内经 task_id 回写。
    """
    from app.services import script_adaptation_service

    db = SessionLocal()
    try:
        with heartbeat_guard(task_id, interval=20):
            script_adaptation_service.build_script_shot_plan(
                db, novel_id, model_id=model_id, per_duration=per_duration,
                task_id=task_id, target_total_seconds=300,
            )
    except Exception as e:
        db.rollback()
        # build_script_shot_plan 失败分支已置 failed，此处兜底（保证 error 文案）
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()


@celery_app.task(name="adapt_script", bind=True)
def adapt_script_task(
    self, task_id: str, novel_id: str,
    model_id: str | None = None, per_duration: int = 15, style: str | None = None,
    style_id: str | None = None, art_style_prompt: str | None = None,
    aspect_ratio: str = "16:9", resolution: str = "720p",
    video_params: dict | None = None,
):
    """剧本改编：小说分析结果 → 结构化剧本 → 落库为 Project。

    分镜时长上限（5/10/15s）：LLM 按镜头内容在 1~上限内配置每镜 duration，
    台词超限自动拆分多分镜；落库时每镜 duration 收敛到上限内。
    resolution：项目视频分辨率（480p/720p，2026-08-16），后续视频按此档位生成。
    """
    from app.services import script_adaptation_service

    db = SessionLocal()
    try:
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=10)
        with heartbeat_guard(task_id, interval=20):
            project = script_adaptation_service.adapt_novel(
                db, novel_id, model_id=model_id, per_duration=per_duration, style=style,
                style_id=style_id, art_style_prompt=art_style_prompt, aspect_ratio=aspect_ratio,
                resolution=resolution, video_params=video_params,
            )
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/projects/{project.id}", finished_at=now(),
        )
        # 2026-08-24 自动预热：项目分镜英文六段式 H3 缓存就绪，出片直接命中、
        # 质量统一（后台任务，不阻塞本项目生成完成返回）
        from app.tasks.prewarm_prompt import prewarm_project_enhance
        prewarm_project_enhance.delay(str(project.id))
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()



@celery_app.task(name="adapt_script_direct", bind=True)
def adapt_script_direct_task(
    self, task_id: str, novel_id: str,
    model_id: str | None = None, per_duration: int = 15, style: str | None = None,
    style_id: str | None = None, art_style_prompt: str | None = None,
    aspect_ratio: str = "16:9", resolution: str = "720p",
    video_params: dict | None = None,
):
    """剧本直读生成项目（无分析）：剧本文本 → 结构化项目（逐集幕）。

    由剧本库「生成项目」按钮触发，跳过小说分析步骤。
    """
    from app.services import script_adaptation_service

    db = SessionLocal()
    try:
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=10)
        with heartbeat_guard(task_id, interval=20):
            project = script_adaptation_service.adapt_script_direct(
                db, novel_id, model_id=model_id, per_duration=per_duration, style=style,
                style_id=style_id, art_style_prompt=art_style_prompt,
                aspect_ratio=aspect_ratio, resolution=resolution,
                video_params=video_params,
            )
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/projects/{project.id}", finished_at=now(),
        )
        # 2026-08-24 自动预热：项目分镜英文六段式 H3 缓存就绪，出片直接命中、
        # 质量统一（后台任务，不阻塞本项目生成完成返回）
        from app.tasks.prewarm_prompt import prewarm_project_enhance
        prewarm_project_enhance.delay(str(project.id))
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()

@celery_app.task(name="write_novel", bind=True)
def write_novel_task(
    self, task_id: str, novel_id: str, brief: str, genre: str, chapters: int,
    model_id: str | None = None, style_mode: str | None = None,
):
    """AI 长篇小说写作：LLM 规划大纲 → 逐章生成正文 → 追加入库（进度实时回传）。

    由 Agent 的 write_novel 工具提交；续写时复用 novel.outline，从断点继续写。
    """
    from app.services import novel_writing_service

    db = SessionLocal()
    try:
        novel = db.get(Novel, novel_id)
        if not novel:
            raise ValueError("小说不存在")
        novel.writing_status = "writing"
        novel.error = None
        db.commit()
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=2)
        with heartbeat_guard(task_id, interval=20):
            # 1. 大纲（续写时复用已有大纲，仅首次生成）
            if not novel.outline:
                novel_writing_service.generate_outline(
                    db, novel, brief, genre, chapters, model_id,
                    style_mode=style_mode,
                )
            outline = novel.outline
            total = len(outline.get("chapters") or [])
            from app.services.novel_analysis_service import split_chapters

            written = len(split_chapters(novel.raw_text))
            # 2. 逐章生成（从断点继续，避免重复写已完成的章节）
            for i in range(written + 1, total + 1):
                text = novel_writing_service.write_chapter(
                    db, novel, i, model_id, style_mode=style_mode
                )
                from app.config import settings
                from app.services import humanize_service as _humanize_svc
                text, _hmeta = _humanize_svc.humanize_text(
                    db, text,
                    style_mode=style_mode or settings.writing_style_default,
                    level=settings.humanize_pass,
                    model_id=model_id,
                )
                novel_writing_service.append_chapter(db, novel, text)
                update_task(
                    db, task_id, status=TaskStatus.running,
                    progress=min(99, int(5 + 93 * i / total)),
                )
        novel.writing_status = "done"
        db.commit()
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/novels/{novel_id}", finished_at=now(),
        )
    except Exception as e:
        db.rollback()
        # 取消保护：任务已取消时不回写 novel 写作失败状态
        t = db.get(Task, task_id)
        if t is not None and t.status == TaskStatus.cancelled:
            return
        novel = db.get(Novel, novel_id)
        if novel:
            novel.writing_status = "failed"
            novel.error = str(e)
            db.commit()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()


@celery_app.task(name="adapt_continuation", bind=True)
def adapt_continuation_task(
    self, task_id: str, novel_id: str, project_id: str,
    chapter_start: int, chapter_end: int,
    model_id: str | None = None,
):
    """章节续接追加：按章节范围改编新幕并续接到已有项目（P6）。"""
    from app.services import script_adaptation_service

    db = SessionLocal()
    try:
        update_task(db, task_id, status=TaskStatus.running, started_at=now(), progress=10)
        with heartbeat_guard(task_id, interval=20):
            result = script_adaptation_service.adapt_novel_continuation(
                db, novel_id, project_id,
                chapter_start=chapter_start, chapter_end=chapter_end,
                model_id=model_id,
            )
        added = len(result["added_episodes"])
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/projects/{project_id}", finished_at=now(),
        )
        return {"added_episodes": added, "chapters": len(result["chapters"])}
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()
