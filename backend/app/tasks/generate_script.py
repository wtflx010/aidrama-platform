"""分集剧本写作 Celery 任务：集纲规划 → 逐集完整剧本 → 逐集追加为项目幕。"""
import json
from uuid import UUID

from sqlalchemy import select

from app.database import SessionLocal
from app.models.project import Project
from app.models.task import Task, TaskStatus, TaskType
from app.providers.errors import map_to_chinese
from app.tasks.base import heartbeat_guard, now, update_task
from app.tasks.celery_app import celery_app


def _humanize(db, text, style_mode, model_id):
    """去AI味审校 pass：复用 humanize_service，按全局 HUMANIZE_PASS 档位执行。"""
    from app.config import settings
    from app.services import humanize_service
    from app.services import writing_style

    return humanize_service.humanize_text(
        db, text,
        style_mode=writing_style.get_style_mode(
            style_mode or settings.writing_style_default
        ),
        level=settings.humanize_pass,
        model_id=model_id,
    )


@celery_app.task(name="write_script_lib", bind=True)
def write_script_lib_task(
    self, task_id: str, novel_id: str,
    brief: str, episodes: int,
    genre: str | None = None, model_id: str | None = None,
    style_mode: str | None = None,
    full_script: str | None = None,
):
    """AI 分集剧本写作 → 剧本库：规划集纲 → 逐集生成完整剧本 → 写入 Novel.raw_text。

    由 Agent 的 write_script 工具提交（2026-08-22 起 write_script 产出「剧本」而非项目）。
    - novel_id 必须指向剧本库中的剧本记录（Novel）
    - 每集剧本以【第X集 标题】标记行开头逐集追加，兼容 split_chapters 分章/分析/改编
    - 集纲存于 Task.provider_task_id，失败重跑复用；续写复用已有大纲从断点继续
    完成后剧本停留在剧本库，由用户「分析 → 生成项目」改编为项目。
    """
    import re

    from app.models.novel import Novel
    from app.services import script_writing_service

    db = SessionLocal()
    try:
        novel = db.get(Novel, novel_id)
        if not novel:
            raise ValueError("剧本不存在")
        if novel.writing_status == "done":
            raise ValueError(f"《{novel.title}》剧本已写完；如需续写新集，请重新规划一部新剧本")
        task = db.get(Task, task_id)
        if not task:
            raise ValueError("任务不存在")
        novel.writing_status = "writing"
        novel.error = None
        db.commit()

        from sqlalchemy import update as _update
        _res = db.execute(
            _update(Task)
            .where(Task.id == task_id, Task.status == TaskStatus.pending)
            .values(status=TaskStatus.running, started_at=now(), progress=2)
        )
        db.commit()
        if _res.rowcount == 0:
            _cur = db.get(Task, task_id)
            if _cur is not None and _cur.status == TaskStatus.succeeded:
                return
            raise ValueError("该剧本写作任务已在执行中，请勿重复发起")

        with heartbeat_guard(task_id, interval=20):
            if task.provider_task_id == "full_script":
                # 2026-08-27：完整剧本正文直写路径——内容已由智能体确认并落库（novel.raw_text），
                # 跳过集纲规划与逐集生成，直接完成标记并走统一收尾（拆幕分镜预览 + 海报）。
                total = 1
                written = 1
            else:
                # 已写集数 = raw_text 中【第X集】标记数
                markers = re.findall(r"【第\d+集[^\n]*】", novel.raw_text or "")
                written = len(markers)

                # 大纲：本任务已存 → 复用；续写（novel 已有大纲）→ 扩集；否则全新规划
                outline = None
                if task.provider_task_id:
                    try:
                        _cand = json.loads(task.provider_task_id)
                        if isinstance(_cand, dict) and _cand.get("episodes"):
                            outline = _cand
                    except Exception:
                        outline = None
                if outline is None and novel.outline and novel.outline.get("episodes"):
                    outline = novel.outline

                if outline is None:
                    outline = script_writing_service.plan_episode_outline(
                        db, brief, episodes, novel.title, genre, model_id,
                        style_mode=style_mode,
                    )
                    novel.outline = outline
                    db.commit()
                total = len(outline.get("episodes") or [])
                if total <= written:
                    raise ValueError(
                        f"剧本已有 {written} 集，目标 {total} 集未增加。"
                        "episodes 表示「追加的集数」，不会覆盖已有内容。"
                    )

                for i in range(written + 1, total + 1):
                    text = script_writing_service.write_episode_script(
                        db, outline, i, model_id, style_mode=style_mode
                    )
                    text, _hmeta = _humanize(db, text, style_mode, model_id)
                    text = text.strip()
                    novel.raw_text = (novel.raw_text.rstrip() + "\n\n" + text) if novel.raw_text.strip() else text
                    from app.services.novel_analysis_service import split_chapters
                    novel.chapters_count = len(split_chapters(novel.raw_text))
                    novel.word_count = len(novel.raw_text)
                    db.commit()
                    task.provider_task_id = json.dumps(outline, ensure_ascii=False)
                    db.commit()
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
                # 2026-08-27：三阶段确认流——写剧本只落剧本库，不再自动连跑 分镜预览/封面。
        # 分镜预览由 generate_shot_preview 在用户确认大纲后生成；封面+建项目由
        # generate_poster_and_project 在用户确认分镜后生成（见 agent/tools/specs.py）。

    except Exception as e:
        db.rollback()
        t = db.get(Task, task_id)
        if t is not None and t.status == TaskStatus.cancelled:
            return
        novel = db.get(Novel, novel_id)
        if novel is not None:
            novel.writing_status = "failed"
            novel.error = str(e)
            db.commit()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()

@celery_app.task(name="write_script", bind=True)
def write_script_task(
    self, task_id: str, project_id: str | None,
    title: str, brief: str, episodes: int,
    genre: str | None = None, model_id: str | None = None,
    style_mode: str | None = None,
):
    """AI 分集剧本写作：LLM 规划集纲 → 逐集生成场次级完整剧本 → 逐集追加为项目幕（进度实时回传）。

    由 Agent 的 write_script 工具提交。
    - project_id 为空：第 1 集时自动新建项目，后续集续接
    - project_id 已存在：从已有幕数（=已写集数）续写，追加新幕；同名资产自动复用
    集纲存于 Task.provider_task_id，失败重跑复用，保证续写前情一致。

    长任务防护：分集任务是长流程（每集多次 LLM 调用），运行期间项目可能被用户删除。
    每轮循环重新从 DB 取项目并校验存在性，被删则友好终止，避免访问已删除实例
    抛 ObjectDeletedError（'Instance <Project ...> has been deleted'）。
    """
    from app.services import script_writing_service

    db = SessionLocal()
    project = None
    project_uuid: UUID | None = None
    try:
        task = db.get(Task, task_id)
        if not task:
            raise ValueError("任务不存在")
        if project_id:
            # 指定项目存在 → 续写；不存在/已被删除 → 回退同题复用或新建，
            # 避免会话记忆中的旧项目 id 导致任务直接失败
            _pid = None
            try:
                _pid = UUID(project_id)
            except Exception:
                _pid = None  # noqa: B904
            if _pid and db.get(Project, _pid):
                project_uuid = _pid
            else:
                _dup = db.scalars(
                    select(Project).where(Project.title == title)
                    .order_by(Project.created_at.desc(), Project.id.desc())
                ).first()
                project_uuid = _dup.id if _dup is not None else None
        else:
            # 去重：未指定项目时，若已存在同题项目则复用（避免与 create_project 并存
            # 两个同名项目；空壳项目也会被填充内容而非旁路新建）。title 匹配取最新。
            _dup = db.scalars(
                select(Project).where(Project.title == title)
                .order_by(Project.created_at.desc(), Project.id.desc())
            ).first()
            if _dup is not None:
                project_uuid = _dup.id
        project = db.get(Project, project_uuid) if project_uuid else None
        # 防并发 ①：同一项目已有 running 的 write_script 任务（不同任务，如重复触发）
        # 时拒绝启动，避免两个任务同时写同一项目导致重复幕。占位 target_id（int=0）不检查。
        if project_uuid is not None:
            _other = db.scalars(
                select(Task).where(
                    Task.type == TaskType.write_script,
                    Task.project_id == project_uuid,
                    Task.status == TaskStatus.running,
                    Task.id != task_id,
                )
            ).first()
            if _other is not None:
                raise ValueError(
                    f"项目「{project.title}」已有分集剧本任务在写作中（{str(_other.id)[:8]}），"
                    "请等待其完成或取消后再发起"
                )
        # 防并发 ②：原子占位——仅当任务仍为 pending 时才置 running。
        # 同一任务被重投递/重复执行时（任务已是 running 或 succeeded），本 UPDATE 影响 0 行，
        # 直接拒绝或幂等返回，杜绝两个执行实例同时写同一项目。
        from sqlalchemy import update as _update
        _res = db.execute(
            _update(Task)
            .where(Task.id == task_id, Task.status == TaskStatus.pending)
            .values(status=TaskStatus.running, started_at=now(), progress=2)
        )
        db.commit()
        if _res.rowcount == 0:
            _cur = db.get(Task, task_id)
            if _cur is not None and _cur.status == TaskStatus.succeeded:
                # 任务已完成，重复执行直接幂等返回（finally 负责关闭 session）
                return
            raise ValueError("该分集剧本任务已在执行中，请勿重复发起")
        with heartbeat_guard(task_id, interval=20):
            outline = script_writing_service.load_or_plan_outline(
                db, task, project, brief, episodes, title, genre, model_id,
                style_mode=style_mode,
            )
            total = len(outline.get("episodes") or [])
            # 已写集数 = 项目现有【有内容】幕数（write_script 一集一幕）。
            # 2026-08-17 修复：create_project 空壳项目会预建 1 个「主幕」（无分镜/无剧本），
            # 若按 len(episodes) 计会把空壳幕误当「已写第 1 集」，导致后续 write_script
            # 从第 2 集开始、第 1 集内容被跳过（任务 succeeded 但项目仍空）。只计有分镜或
            # video_script 的有效幕，空壳幕在写第 1 集时被填充而非跳过。
            from app.models.segment import Segment
            from app.models.project import Episode as _Episode
            if project is not None:
                ep_ids = [e.id for e in project.episodes]
                valid_indices: set[int] = set()
                for ep in project.episodes:
                    has_seg = db.scalar(
                        select(Segment.id).where(Segment.episode_id == ep.id).limit(1)
                    )
                    # 有效幕须真实存在分镜。create_project 空壳项目会预建一个「主幕」：
                    # 无分镜但可能有预写的 video_script（materialize_draft 为之生成幕级
                    # 叙事），若把 video_script 也算有效，会重蹈「空壳幕误当已写第 1 集」。
                    if has_seg is not None:
                        valid_indices.add(ep.index)
                written = (max(valid_indices) + 1) if valid_indices else 0
                # 幂等防护：已有有效幕视为「已写集」，跳过；空壳幕（index 无有效内容）
                # 不进入 existing，写第 1 集时 materialize_draft 续接会填充它
                existing = set(valid_indices)
                # 空壳幕清理（2026-08-17）：written==0 且项目只有空壳幕（无分镜）时，
                # 先把空壳幕删除，让第 1 集以 index=0 正式新建（避免残留「主幕」空行，
                # 也避免空壳幕与第 1 集 index 撞车）。仅在有内容写入前做，safe 幂等。
                if written == 0 and project.episodes:
                    for ep in project.episodes:
                        if ep.index not in valid_indices:
                            db.delete(ep)
                    db.commit()
            else:
                written = 0
                existing = set()
            # 兜底防「假成功」空循环：若目标集数 <= 已写有效幕数（参数语义错配或
            # 重复触发），直接报错而不是静默 succeeded，让调用方/用户感知问题。
            if total <= written:
                raise ValueError(
                    f"追加 {episodes} 集未生效：项目已有 {written} 个有效幕。"
                    "请确认参数语义——episodes 表示「追加的集数」，不会覆盖已有内容。"
                    "已停止任务，未做任何修改。"
                )
            for i in range(written + 1, total + 1):
                # 长任务防护：每轮重新校验项目存在性（运行中可能被删除）
                if project_uuid is not None:
                    fresh = db.get(Project, project_uuid)
                    if fresh is None:
                        raise ValueError("项目已被删除，分集剧本写作已终止")
                    project = fresh
                if project is not None and (i - 1) in existing:
                    # 该集幕已存在（重复执行/恢复竞态）→ 跳过，不重复写
                    existing.discard(i - 1)
                    update_task(
                        db, task_id, status=TaskStatus.running,
                        progress=min(99, int(3 + 95 * i / total)),
                    )
                    continue
                ep = outline["episodes"][i - 1]
                script = script_writing_service.write_episode_script(
                    db, outline, i, model_id, style_mode=style_mode
                )
                script, _hmeta = _humanize(db, script, style_mode, model_id)
                ep_draft = script_writing_service.structure_episode(
                    db, script, i, ep.get("title") or "", model_id
                )
                project = script_writing_service.apply_episode(
                    db, project, ep_draft, script, i, per_duration=15, project_title=title
                )
                # 第一集新建项目后，把任务关联到真实项目（此前 target_id 为占位 uuid）
                if task.target_id.int == 0:
                    task.target_id = project.id
                    task.project_id = project.id
                    db.commit()
                update_task(
                    db, task_id, status=TaskStatus.running,
                    progress=min(99, int(3 + 95 * i / total)),
                )
        if project is None:
            raise ValueError("项目未创建")
        update_task(
            db, task_id, status=TaskStatus.succeeded, progress=100,
            result_url=f"/projects/{project.id}", finished_at=now(),
        )
        # 2026-08-24 自动预热：新追加幕的分镜英文六段式 H3 缓存就绪（后台，不阻塞）
        from app.tasks.prewarm_prompt import prewarm_project_enhance
        prewarm_project_enhance.delay(str(project.id))
    except Exception as e:
        db.rollback()
        update_task(db, task_id, status=TaskStatus.failed, error=map_to_chinese(e), finished_at=now())
    finally:
        db.close()
