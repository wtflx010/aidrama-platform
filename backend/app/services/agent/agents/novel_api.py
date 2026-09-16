"""智能体工作流层：长篇小说写作网关（大纲 / 提交任务）。

从 agent_service.py 剥离（原行号 5733~5780 区域），逻辑未改动。
"""

from sqlalchemy.orm import Session


def generate_novel_outline(
    db: Session, brief: str, genre: str | None = None,
    chapters: int = 10, model_id=None,
) -> dict:
    """生成小说大纲（不落库），供前端预览确认后提交写作。"""
    from app.services import novel_writing_service

    return novel_writing_service.plan_outline(
        db, brief, genre or "", chapters, model_id
    )


def submit_novel_writing(
    db: Session, title: str, brief: str, genre: str | None = None,
    chapters: int = 10, outline: dict | None = None, model_id=None,
) -> tuple:
    """创建小说（含已确认大纲，可选）+ 提交后台逐章写作任务。返回 (novel_id, task_id, title)。

    outline 为空时由写作任务自动规划大纲；续写场景由 write_novel 工具处理。
    """
    from app.models.novel import Novel
    from app.models.task import Task, TaskStatus, TaskType
    from app.tasks.generate_novel import write_novel_task

    title = (title or "").strip()
    if not title:
        raise ValueError("小说标题不能为空")
    try:
        chapters = int(chapters or 10)
    except (TypeError, ValueError):
        chapters = 10
    novel = Novel(
        title=title[:200],
        raw_text="",
        writing_status="none",
        outline=outline or None,
    )
    db.add(novel)
    db.commit()
    db.refresh(novel)

    task = Task(
        type=TaskType.write_novel,
        target_type="novel",
        target_id=novel.id,
        status=TaskStatus.pending,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    write_novel_task.delay(
        str(task.id), str(novel.id), brief, genre or "", chapters,
        str(model_id) if model_id else None,
    )
    return novel.id, task.id, novel.title
