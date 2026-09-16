"""项目业务服务。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Episode, Project, ProjectStatus
from app.schemas.project import AIGenerateBody, ProjectCreate, ProjectUpdate


def list_projects(db: Session, status: str | None = None):
    q = select(Project).order_by(Project.created_at.desc())
    if status:
        q = q.where(Project.status == status)
    return db.scalars(q).all()


def get(db: Session, project_id) -> Project | None:
    return db.get(Project, project_id)


def create(db: Session, payload: ProjectCreate) -> Project:
    p = Project(
        title=payload.title,
        synopsis=payload.synopsis,
        script=payload.script,
        aspect_ratio=payload.aspect_ratio,
        resolution=payload.resolution,
        style_id=payload.style_id,
        art_style_prompt=payload.art_style_prompt,
        rules=payload.rules,
        video_params=payload.video_params or {},
    )
    db.add(p)
    db.flush()
    db.add(Episode(project_id=p.id, index=0, title="主幕"))
    db.commit()
    db.refresh(p)
    return p


def update(db: Session, project_id, payload: ProjectUpdate) -> Project | None:
    p = db.get(Project, project_id)
    if not p:
        return None
    data = payload.model_dump(exclude_unset=True)
    style_changed = "style_id" in data or "art_style_prompt" in data
    for k, v in data.items():
        setattr(p, k, v)
    if style_changed:
        # 风格变更 → 清除该项目所有分镜的增强缓存（旧风格生成的 prompt 失效）
        from app.models.segment import Segment
        seg_ids = select(Episode.id).where(Episode.project_id == p.id)
        db.query(Segment).filter(Segment.episode_id.in_(seg_ids)).update(
            {
                Segment.enhanced_prompt: None,
                Segment.enhanced_negative_prompt: None,
                Segment.enhanced_target: None,
            },
            synchronize_session=False,
        )
    db.commit()
    db.refresh(p)
    return p


def delete(db: Session, project_id) -> bool:
    """删除项目：级联删 DB 行（幕/分镜/关键帧/视频/配音/字幕/任务/成片）
    + 同步清理项目产生的磁盘媒体文件。

    2026-08-24 项目专用美术资产：
    - 项目的美术资产随项目删除——归属本项目的资产整行 + 生成的图片一并删除；
      绑定到本项目但仍有其它项目使用（共享）的资产仅解绑保留，避免误删；
      绑定到本项目且无其它归属的资产一并删除。
    - BGM / SFX 音频库保持不变：解绑保留（project_bgm / project_sfx 行删除，
      bgm.project_id / sfx.project_id 置空），音频保留在库内。
    - 分镜/关键帧/视频/配音/任务/成片等只属于项目的资源仍级联删除。
    """
    import os
    import shutil

    from sqlalchemy import delete, select, update

    from app.config import settings
    from app.models.asset import Asset, ProjectAsset
    from app.models.bgm import BgmTrack, ProjectBgm
    from app.models.segment import Segment
    from app.models.sfx import SfxClip
    from app.utils.media import delete_media_file

    p = db.get(Project, project_id)
    if not p:
        return False

    # 1) 收集仅属于项目的媒体磁盘文件 URL（删 DB 行前收集）
    urls: list[str] = []
    for ep in p.episodes:
        for seg in ep.segments:
            for kf in seg.keyframes:
                urls.append(kf.image_url)
            for v in seg.videos:
                urls.append(v.video_url)
                urls.append(v.first_frame_url)
                urls.append(v.last_frame_url)
            for vl in seg.voice_lines:
                urls.append(vl.audio_url)
    for t in p.tasks:
        urls.append(t.result_url)

    # 2) 项目美术资产随项目删除（2026-08-24，项目专用资产）：
    #    - 归属本项目的资产：整行 + 生成的图片一并删除；
    #    - 绑定到本项目但归属其它项目/全局且仍被其它项目使用的共享资产：
    #      仅解除对本项目的绑定，保留（不误删共享资产）；
    #    - 绑定到本项目且无其它归属（等效本项目专用）的资产：随项目删除。
    # 音频库保持不变（BGM/SFX 仍解绑保留）。
    from app.services.asset_service import delete_with_media as _delete_asset_media
    from app.services.asset_service import project_ids_of as _pid_of
    owned_ids = db.scalars(select(Asset.id).where(Asset.project_id == project_id)).all()
    bound_ids = db.scalars(select(ProjectAsset.asset_id).where(ProjectAsset.project_id == project_id)).all()
    asset_ids = set(str(x) for x in owned_ids) | set(str(x) for x in bound_ids)
    # 先移除本项目的绑定行，再逐条判定删除
    db.execute(delete(ProjectAsset).where(ProjectAsset.project_id == project_id))
    db.flush()
    import uuid as _uuid
    for aid in asset_ids:
        a = db.get(Asset, _uuid.UUID(aid))
        if not a:
            continue
        if a.project_id == project_id or not _pid_of(db, _uuid.UUID(aid)):
            _delete_asset_media(db, _uuid.UUID(aid))
    # 音频库：BGM / SFX 解绑保留
    db.execute(update(BgmTrack).where(BgmTrack.project_id == project_id).values(project_id=None))
    db.execute(delete(ProjectBgm).where(ProjectBgm.project_id == project_id))
    # SFX：项目内音效解绑保留（挂分镜的片段，segment 删除时随之 SET NULL 到库）
    sfx_ids = db.scalars(
        select(SfxClip.id)
        .join(Segment, SfxClip.segment_id == Segment.id)
        .join(Episode, Segment.episode_id == Episode.id)
        .where(Episode.project_id == project_id)
    ).all()
    if sfx_ids:
        db.execute(update(SfxClip).where(SfxClip.id.in_(sfx_ids)).values(project_id=None))

    # 3) 级联删除项目自身 DB 行
    db.delete(p)
    db.commit()

    # 4) 清理磁盘文件（仅项目自身媒体；资产/音频库文件保留）
    for u in dict.fromkeys(urls):
        try:
            delete_media_file(u)
        except Exception:
            continue
    export_dir = os.path.join(settings.export_dir, str(project_id))
    if os.path.isdir(export_dir):
        shutil.rmtree(export_dir, ignore_errors=True)
    return True


# ===== 一句话生成项目（LLM） =====

def ai_generate(db: Session, payload: AIGenerateBody) -> Project:
    """一句话梗概 → LLM → 创建项目（含多幕 + 分镜 + 资产自动关联）。同步返回。

    委托 llm_script_service 完成实际工作：
    1. generate_draft：LLM 返回 assets + episodes + segments
    2. materialize_draft：落库并自动关联角色/场景/道具到分镜

    per_duration：分镜时长上限（5/10/15s），LLM 按镜头内容在 1~上限内配置每镜
    duration；台词超限自动拆分为多个连续分镜，落库时每镜 duration 收敛到上限内。
    """
    from app.services.llm_script_service import generate_draft, materialize_draft

    # 解析有效风格 prompt，注入 LLM 剧本生成
    from app.services.style_service import get_effective_style_prompt
    style_hint: str | None = None
    if payload.style_id or payload.art_style_prompt:
        # 用临时 Project 对象解析（尚未落库）
        from app.models.project import Project as _P
        tmp = _P(style_id=payload.style_id, art_style_prompt=payload.art_style_prompt)
        style_hint = get_effective_style_prompt(db, tmp)

    draft = generate_draft(
        db,
        payload.synopsis,
        model_id=payload.model_id,
        episode_count=payload.episode_count,
        style_hint=style_hint,
        per_duration=payload.per_duration or 15,
    )
    return materialize_draft(
        db,
        draft,
        synopsis=payload.synopsis,
        aspect_ratio=payload.aspect_ratio,
        resolution=payload.resolution or "720p",
        style_id=payload.style_id,
        art_style_prompt=payload.art_style_prompt,
        per_duration=payload.per_duration,
        video_params=payload.video_params or {},
    )
