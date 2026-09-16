"""修片工作流 API（P1-4）：reframe / voice-change / draw-to-video 触发。（target_id 用随机 UUID，任务无对应媒体行）

POST /rework/reframe        {video_url, target_ratio, resolution}
POST /rework/voice-change   {video_url, text, voice_id?, emotion?}
POST /rework/draw-to-video  {video_url, prompt, sketch_url?}
均返回 {task_id}，结果 URL 通过任务中心轮询（result_url）。
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task, TaskStatus, TaskType

router = APIRouter()


class ReframeIn(BaseModel):
    video_url: str
    target_ratio: str = "9:16"
    resolution: str = "720p"


class VoiceChangeIn(BaseModel):
    video_url: str
    text: str
    voice_id: str | None = None
    emotion: str | None = None


class DrawToVideoIn(BaseModel):
    video_url: str
    prompt: str
    sketch_url: str | None = None
    ratio: str = "16:9"


def _mk_task(db, project_id, ttype, target_id):
    task = Task(project_id=project_id, type=ttype, target_type="rework",
                target_id=target_id, status=TaskStatus.pending)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/rework/reframe", status_code=201)
def trigger_reframe(payload: ReframeIn, db: Session = Depends(get_db)):
    from app.tasks.rework_video import reframe_video
    task = _mk_task(db, None, TaskType.reframe_video, str(uuid.uuid4()))
    reframe_video.delay(str(task.id), payload.video_url, payload.target_ratio, payload.resolution)
    return {"task_id": str(task.id)}


@router.post("/rework/voice-change", status_code=201)
def trigger_voice_change(payload: VoiceChangeIn, db: Session = Depends(get_db)):
    from app.tasks.rework_video import voice_change_video
    task = _mk_task(db, None, TaskType.voice_change_video, str(uuid.uuid4()))
    voice_change_video.delay(str(task.id), payload.video_url, payload.text,
                             payload.voice_id, payload.emotion)
    return {"task_id": str(task.id)}


@router.post("/rework/draw-to-video", status_code=201)
def trigger_draw_to_video(payload: DrawToVideoIn, db: Session = Depends(get_db)):
    from app.tasks.rework_video import draw_to_video
    task = _mk_task(db, None, TaskType.draw_to_video, str(uuid.uuid4()))
    draw_to_video.delay(str(task.id), payload.video_url, payload.prompt, payload.sketch_url, payload.ratio)
    return {"task_id": str(task.id)}
