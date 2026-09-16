"""导出业务服务：收集导出片段（幕级视频优先，逐镜回退），发起成片导出任务；支持删除成片。"""
import json
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.episode_video import EpisodeVideo
from app.models.media import VideoClip, MediaStatus
from app.models.project import Episode, Project
from app.models.segment import Segment
from app.models.task import Task, TaskStatus, TaskType
from app.utils.media import delete_media_file


def _collect_video_clips(db: Session, project_id):
    """逐镜回退：项目下每个分镜「最新一条成功且有视频文件」的片段，按幕→分镜顺序。

    与前端展示一致：前端每个分镜只显示最新一条视频。重新生成时旧片段已被删除，
    这里用 row_number 按 segment 去重兜底，避免历史/废弃片段重复合并进成片。
    """
    ranked = db.execute(
        select(
            VideoClip.id,
            func.row_number().over(
                partition_by=VideoClip.segment_id,
                # 超清版优先：同一分镜存在 480p 母片与超清版时，导出取 is_upscaled 超清版
                order_by=(VideoClip.is_upscaled.desc().nulls_last(), VideoClip.created_at.desc()),
            ).label("rn"),
        )
        .join(Segment, VideoClip.segment_id == Segment.id)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(
            Episode.project_id == project_id,
            VideoClip.status == MediaStatus.succeeded,
            VideoClip.video_url.is_not(None),
        )
    ).all()
    ids = [r[0] for r in ranked if r[1] == 1]
    if not ids:
        return []
    return db.scalars(
        select(VideoClip)
        .join(Segment, VideoClip.segment_id == Segment.id)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(VideoClip.id.in_(ids))
        .order_by(
            Episode.index.asc(),
            Segment.index.asc(),
            VideoClip.created_at.asc(),
        )
    ).all()


def collect_export_items(db: Session, project_id, episode_id=None) -> list[dict]:
    """收集导出片段（P7 幕级视频优先，逐镜回退），按幕→段顺序。

    返回统一结构列表（供 export_film / export_episode 按 kind 分支处理）：
      {"kind": "episode_video", "item_id", "episode_id", "video_url",
       "segment_ids": [...], "segments": [覆盖的分镜]}   # 幕级视频段
      {"kind": "video_clip", "item_id", "segment_id", "episode_id", "video_url"}
    episode_id 非空时只收集该幕（剧集导出）。
    """
    q = select(Episode).where(Episode.project_id == project_id)
    if episode_id is not None:
        q = q.where(Episode.id == episode_id)
    episodes = db.scalars(q.order_by(Episode.index.asc())).all()
    # 预计算逐镜回退片段（只查一次窗口），else 分支直接遍历，避免循环内重复窗口查询
    fallback_clips = _collect_video_clips(db, project_id)

    items: list[dict] = []
    for ep in episodes:
        ep_videos = db.scalars(
            select(EpisodeVideo)
            .where(
                EpisodeVideo.episode_id == ep.id,
                EpisodeVideo.status == MediaStatus.succeeded,
                EpisodeVideo.video_url.is_not(None),
            )
            .order_by(EpisodeVideo.index.asc())
        ).all()
        if ep_videos:
            segs = db.scalars(
                select(Segment).where(Segment.episode_id == ep.id).order_by(Segment.index.asc())
            ).all()
            for ev in ep_videos:
                # P7.6：每幕 1 行视频，覆盖该幕全部分镜
                items.append({
                    "kind": "episode_video",
                    "item_id": ev.id,
                    "episode_id": ep.id,
                    "video_url": ev.video_url,
                    "segment_ids": [s.id for s in segs],
                    "segments": segs,
                })
        else:
            # 逐镜回退（兼容存量项目）：遍历预计算片段，避免循环内重复窗口查询
            for c in fallback_clips:
                seg = db.get(Segment, c.segment_id)
                if seg and seg.episode_id == ep.id:
                    items.append({
                        "kind": "video_clip",
                        "item_id": c.id,
                        "segment_id": c.segment_id,
                        "episode_id": ep.id,
                        "video_url": c.video_url,
                    })
    return items


def list_by_project(db: Session, project_id):
    """项目下的导出任务列表（按时间倒序）。"""
    return db.scalars(
        select(Task).where(
            Task.project_id == project_id,
            Task.type == TaskType.export_film,
        ).order_by(Task.created_at.desc())
    ).all()


def export(db: Session, project_id, include_voice: bool = True,
           include_subtitle: bool = True, burn_subtitle: bool = True,
           include_bgm: bool = True, include_sfx: bool = True) -> Task:
    """发起导出：校验有可导出片段 → 建任务 → 投递 export_film。"""
    from app.tasks.generate_export import export_film

    project = db.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")
    items = collect_export_items(db, project_id)
    if not items:
        raise ValueError("没有可导出的视频片段，请先完成幕级视频或分镜的图生视频")

    task = Task(
        project_id=project_id,
        type=TaskType.export_film,
        target_type="project",
        target_id=project_id,
        status=TaskStatus.pending,
    )
    # 2026-08-09（P2-2）：导出选项参数持久化到 provider_task_id（导出任务不使用
    # provider 轮询，该字段空闲）。批量重跑失败任务时据此恢复原选项——
    # 否则重跑默认全开（含 BGM/字幕/硬字幕），与首次导出选项不一致。
    task.provider_task_id = _export_options_json(
        include_voice, include_subtitle, burn_subtitle, include_bgm, include_sfx
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    export_film.delay(
        str(task.id),
        include_voice=include_voice,
        include_subtitle=include_subtitle,
        burn_subtitle=burn_subtitle,
        include_bgm=include_bgm,
        include_sfx=include_sfx,
    )
    return task


def _export_options_json(include_voice, include_subtitle, burn_subtitle, include_bgm, include_sfx) -> str:
    """导出选项参数持久化（批量重跑失败任务时恢复，见 task_service.retry_failed）。"""
    return json.dumps({
        "include_voice": bool(include_voice),
        "include_subtitle": bool(include_subtitle),
        "burn_subtitle": bool(burn_subtitle),
        "include_bgm": bool(include_bgm),
        "include_sfx": bool(include_sfx),
    })


def _cover_url_from_result(result_url: str | None) -> str | None:
    """剧集成片封面 URL：与成片同目录同名，扩展名 .mp4 → _cover.jpg。"""
    if not result_url:
        return None
    return result_url[:-4] + "_cover.jpg" if result_url.endswith(".mp4") else None


def _delete_episode_exports(db: Session, episode_id) -> None:
    """覆盖语义：删除该幕此前全部剧集导出任务（DB 行 + 磁盘成片与封面）。

    剧集导出每幕只保留最新一个成片：重新导出前清掉旧任务与旧文件，
    新导出写固定文件名 ep_{episode_id}.mp4 覆盖。
    """
    tasks = db.scalars(
        select(Task).where(
            Task.type == TaskType.export_episode,
            Task.target_type == "episode",
            Task.target_id == episode_id,
        )
    ).all()
    for t in tasks:
        delete_media_file(t.result_url)
        delete_media_file(_cover_url_from_result(t.result_url))
        db.delete(t)
    if tasks:
        db.commit()


def get_episode_export(db: Session, episode_id) -> dict:
    """该幕最新剧集导出任务 + 可导出性判断（供前端展示准备状态）。"""
    task = db.scalar(
        select(Task).where(
            Task.type == TaskType.export_episode,
            Task.target_type == "episode",
            Task.target_id == episode_id,
        ).order_by(Task.created_at.desc())
    )
    exportable = False
    reason = None
    if task is None or task.status in (TaskStatus.failed, TaskStatus.cancelled):
        # 无任务或上次失败：重新判断该幕是否有可导出视频
        ep = db.get(Episode, episode_id)
        if not ep:
            reason = "幕不存在"
        else:
            items = collect_export_items(db, ep.project_id, episode_id=episode_id)
            exportable = bool(items)
            if not exportable:
                reason = "该幕尚无视频，请先完成幕级视频或分镜的图生视频"
    else:
        exportable = True
    return {
        "task": task,
        "exportable": exportable,
        "reason": reason,
        "file_size": _result_file_size(task),
    }


def _result_file_size(task: Task | None) -> int | None:
    """成片磁盘文件字节数（供前端展示文件大小；文件缺失/未完成时 None）。"""
    if not task or not task.result_url or task.status != TaskStatus.succeeded:
        return None
    rel = task.result_url.split("/exports/", 1)
    if len(rel) != 2:
        return None
    local = os.path.join(settings.export_dir, rel[1])
    try:
        return os.path.getsize(local) if os.path.isfile(local) else None
    except OSError:
        return None


def export_episode(db: Session, episode_id, include_voice: bool = False,
                   include_subtitle: bool = True, burn_subtitle: bool = True,
                   include_bgm: bool = True, include_sfx: bool = True) -> Task:
    """发起单幕（剧集）导出：收集该幕片段 → 覆盖旧成片 → 建任务 → 投递 export_episode。

    覆盖语义：同一幕多次导出只保留最新一个成片（旧任务/旧文件在导出前删除）。
    """
    from app.tasks.generate_export import export_episode as export_episode_task

    ep = db.get(Episode, episode_id)
    if not ep:
        raise ValueError("幕不存在")
    items = collect_export_items(db, ep.project_id, episode_id=episode_id)
    if not items:
        raise ValueError("该幕没有可导出的视频片段，请先完成幕级视频或分镜的图生视频")

    # 覆盖旧成片（DB 行 + 磁盘 mp4/封面）
    _delete_episode_exports(db, episode_id)

    task = Task(
        project_id=ep.project_id,
        type=TaskType.export_episode,
        target_type="episode",
        target_id=episode_id,
        status=TaskStatus.pending,
    )
    task.provider_task_id = _export_options_json(
        include_voice, include_subtitle, burn_subtitle, include_bgm, include_sfx
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    export_episode_task.delay(
        str(task.id),
        include_voice=include_voice,
        include_subtitle=include_subtitle,
        burn_subtitle=burn_subtitle,
        include_bgm=include_bgm,
        include_sfx=include_sfx,
    )
    return task


def delete(db: Session, task_id) -> bool:
    """删除成片：删除磁盘上的成片文件（result_url）+ 任务行。

    成片文件为 exports/{project_id}/film.mp4，删除后空目录一并清理；
    再次导出会自动重建。若任务正在导出中，置为 cancelled 后由 worker 自行退出。
    """
    task = db.get(Task, task_id)
    if not task or task.type not in (TaskType.export_film, TaskType.export_episode):
        return False
    if task.status in (TaskStatus.pending, TaskStatus.running):
        task.status = TaskStatus.cancelled
        task.error = "成片已删除，任务取消"
    delete_media_file(task.result_url)
    # 剧集成片封面（ep_{id}_cover.jpg）一并删除
    if task.type == TaskType.export_episode:
        delete_media_file(_cover_url_from_result(task.result_url))
    db.delete(task)
    db.commit()
    return True
