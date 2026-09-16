"""分镜 API：按项目/幕列表、增删改、重排、资产绑定。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.project import Episode
from app.models.segment import Segment
from app.schemas.segment import (
    ReorderBody,
    SegmentAssetBinding,
    SegmentAudioBind,
    SegmentCreate,
    SegmentEnhanceIn,
    SegmentOut,
    SegmentUpdate,
)
from app.services import segment_service

router = APIRouter()


@router.get("/projects/{project_id}/segments", response_model=list[SegmentOut])
def list_by_project(project_id: UUID, db: Session = Depends(get_db)):
    return segment_service.list_by_project(db, project_id)


@router.get("/episodes/{episode_id}/segments", response_model=list[SegmentOut])
def list_by_episode(episode_id: UUID, db: Session = Depends(get_db)):
    return segment_service.list_by_episode(db, episode_id)


@router.post("/episodes/{episode_id}/segments", response_model=SegmentOut, status_code=201)
def create(episode_id: UUID, payload: SegmentCreate, db: Session = Depends(get_db)):
    if not db.get(Episode, episode_id):
        raise HTTPException(404, "幕不存在")
    return segment_service.create(db, episode_id, payload)


@router.put("/segments/{segment_id}", response_model=SegmentOut)
def update(segment_id: UUID, payload: SegmentUpdate, db: Session = Depends(get_db)):
    s = segment_service.update(db, segment_id, payload)
    if not s:
        raise HTTPException(404, "分镜不存在")
    return s


@router.post("/segments/{segment_id}/audio/bind")
def bind_audio(segment_id: UUID, body: SegmentAudioBind, db: Session = Depends(get_db)):
    """把全局音频库的 BGM/SFX 绑定到分镜。

    - BGM：写入所属幕（BgmTrack.episode_id），并补齐 project_id 供成片导出混音
    - SFX：写入分镜（SfxClip.segment_id）
    - clear_bgm / clear_sfx＝True 时解除当前绑定（等同于「不指定」）
    """
    from sqlalchemy import select

    from app.models.bgm import BgmTrack
    from app.models.sfx import SfxClip

    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(404, "分镜不存在")

    if body.clear_bgm:
        for t in db.scalars(select(BgmTrack).where(BgmTrack.episode_id == seg.episode_id)).all():
            t.episode_id = None
    elif body.bgm_id is not None:
        track = db.get(BgmTrack, body.bgm_id)
        if not track:
            raise HTTPException(404, "BGM 不存在")
        track.episode_id = seg.episode_id
        if track.project_id is None and seg.episode is not None:
            track.project_id = seg.episode.project_id

    if body.clear_sfx:
        for clip in db.scalars(select(SfxClip).where(SfxClip.segment_id == segment_id)).all():
            clip.segment_id = None
    elif body.sfx_id is not None:
        clip = db.get(SfxClip, body.sfx_id)
        if not clip:
            raise HTTPException(404, "音效不存在")
        clip.segment_id = segment_id

    db.commit()
    return {"ok": True}


@router.patch("/segments/{segment_id}/assets", response_model=SegmentOut)
def bind_assets(segment_id: UUID, body: SegmentAssetBinding, db: Session = Depends(get_db)):
    """给分镜绑定角色/场景/道具（UUID 转字符串以兼容 JSONB）。"""
    update_data: dict = {}
    if body.character_ids is not None:
        update_data["character_ids"] = [str(cid) for cid in body.character_ids]
    # scene_id 用 fields_set 判断：显式传 null 表示取消场景选择（不能按 is not None 跳过）
    if "scene_id" in body.model_fields_set:
        update_data["scene_id"] = str(body.scene_id) if body.scene_id is not None else None
    if body.prop_ids is not None:
        update_data["prop_ids"] = [str(pid) for pid in body.prop_ids]
    if not update_data:
        raise HTTPException(400, "未提供任何资产绑定字段")
    s = segment_service.update(db, segment_id, SegmentUpdate(**update_data))
    if not s:
        raise HTTPException(404, "分镜不存在")
    return s


@router.post("/segments/{segment_id}/enhance-prompt")
def enhance_prompt(segment_id: UUID, body: SegmentEnhanceIn, db: Session = Depends(get_db)):
    """分镜提示词扩写(H3 风格,分镜级缓存):画布"扩写"按钮的后端。"""
    from app.models.segment import Segment
    from app.services.prompt_enhance_service import ensure_enhanced_prompt

    seg = db.get(Segment, segment_id)
    if not seg:
        raise HTTPException(404, "分镜不存在")
    if body.prompt and body.prompt.strip():
        seg.description = body.prompt.strip()
        db.commit()
    lang = (body.lang or "zh").strip().lower()
    if lang not in ("zh", "en"):
        lang = "zh"
    try:
        prompt, negative = ensure_enhanced_prompt(
            db, seg, seg.episode.project if seg.episode else None,
            target=body.target or "image",
            force=bool(body.force),
            lang=lang,
        )
    except Exception as e:  # noqa: BLE001 - LLM 失败时 ensure 内部已回退默认,此处兜底
        raise HTTPException(400, f"扩写失败:{e}")
    # 视频（H3 Ref2VA 六段式）：生成「中英对照」版并写回分镜提示词落库持久化——
    # 用户跨分镜切换仍保留、可编辑；生成链路 worker 使用同源英文六段式缓存执行。
    # 图片 target 保持原行为（不写回 desc，仅返回结构化结果）。
    bilingual_prompt = prompt
    if (body.target or "image") == "video":
        from app.services.prompt_enhance_service import to_bilingual_six_sections

        bilingual_prompt = to_bilingual_six_sections(prompt)
        seg.description = bilingual_prompt
        # 关键：提示词已变更 → 必须清除增强缓存。否则 worker 重新生成时仍命中
        # 旧缓存（基于结构化前的描述），导致「结构化后重新生成与之前一模一样」。
        # 清空后下次生成缓存 miss，会基于结构化后的 hint 重新 H3 增强出片。
        from app.services.segment_service import clear_enhanced_prompt as _clear_enh

        _clear_enh(db, segment_id)
        db.commit()
        db.refresh(seg)
    return {
        "enhanced_prompt": prompt,
        "negative_prompt": negative,
        "bilingual_prompt": bilingual_prompt,
    }


@router.post("/segments/{segment_id}/polish")
def polish_segment_api(segment_id: UUID, db: Session = Depends(get_db)):
    """AI 润色单分镜（同步 LLM）：描述更有画面感、对白更口语化、旁白更精炼。"""
    from app.services.script_polish_service import polish_segment

    try:
        result = polish_segment(db, str(segment_id))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.delete("/segments/{segment_id}")
def delete(segment_id: UUID, db: Session = Depends(get_db)):
    if not segment_service.delete(db, segment_id):
        raise HTTPException(404, "分镜不存在")
    return {"ok": True}


@router.post("/episodes/{episode_id}/segments/reorder", response_model=list[SegmentOut])
def reorder(episode_id: UUID, body: ReorderBody, db: Session = Depends(get_db)):
    return segment_service.reorder(db, episode_id, body.ordered_ids)
