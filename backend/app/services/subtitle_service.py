"""字幕业务服务：同步生成（按标点切分 + 字数比例对齐）+ 手动编辑。"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import CHARS_PER_SEC
from app.models.segment import Segment
from app.models.voice import Subtitle
from app.models.voice import VoiceLine
from app.schemas.voice import SubtitleUpdate


def list_by_segment(db: Session, segment_id):
    return db.scalars(
        select(Subtitle).where(Subtitle.segment_id == segment_id).order_by(Subtitle.start_ms.asc())
    ).all()


def _strip_print_prefix(text: str) -> str:
    """去除字幕文本开头的说话人/旁白前缀（如「小云：」「旁白：」），仅保留正文。"""
    t = (text or "").strip()
    for p in ("旁白：", "旁白:", "叙述者：", "叙述者:", "画外音：", "画外音:", "【旁白】", "（旁白）", "(旁白)", "旁白"):
        if t.startswith(p):
            t = t[len(p):].strip()
            break
    import re as _re
    m = _re.match(r"^[\u4e00-\u9fffA-Za-z0-9·]{1,12}[：:]\s*", t)
    if m:
        t = t[m.end():].strip()
    return t


def _split_by_punctuation(text: str, max_chars: int = 18) -> list[str]:
    """按中英文标点切分文本，每块不超过 max_chars 字。"""
    # 按标点切分，保留标点
    parts = re.split(r"([，。！？；,!?;])", text)
    chunks: list[str] = []
    cur = ""
    for p in parts:
        if not p:
            continue
        cur += p
        if len(cur) >= max_chars or p in "，。！？；,!?;":
            chunks.append(cur.strip())
            cur = ""
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if c]


def generate(db: Session, segment_id) -> list[Subtitle]:
    """同步生成字幕：从对白/旁白按标点切分，字数比例分配时间。

    总时长优先取该分镜最新配音的 duration，其次 segment.duration，最后按 CHARS_PER_SEC（5.5 字/秒）估算。
    """
    segment = db.get(Segment, segment_id)
    if not segment:
        raise ValueError("分镜不存在")
    text = segment.dialogue or segment.narration
    if not text:
        raise ValueError("分镜无对白/旁白，无法生成字幕")
    # 2026-09-02：字幕只显示台词/旁白正文，去除"人物名："/"旁白："前缀
    text = _strip_print_prefix(text)

    # 删除旧字幕
    for old in db.scalars(select(Subtitle).where(Subtitle.segment_id == segment_id)).all():
        db.delete(old)

    # 总时长：优先配音 duration，其次 segment.duration，最后按字数估算
    vl = db.scalar(
        select(VoiceLine).where(VoiceLine.segment_id == segment_id).order_by(VoiceLine.created_at.desc())
    )
    if vl and vl.duration:
        total_ms = int(vl.duration * 1000)
    else:
        total_ms = max(int(segment.duration * 1000), int(len(text) / CHARS_PER_SEC * 1000))

    chunks = _split_by_punctuation(text)
    total_chars = sum(len(c) for c in chunks) or 1
    subs: list[Subtitle] = []
    cursor_ms = 0
    for chunk in chunks:
        dur_ms = int(total_ms * len(chunk) / total_chars)
        sub = Subtitle(segment_id=segment_id, text=chunk, start_ms=cursor_ms, end_ms=cursor_ms + dur_ms)
        subs.append(sub)
        db.add(sub)
        cursor_ms += dur_ms
    if subs:
        subs[-1].end_ms = total_ms  # 末块对齐总时长
    db.commit()
    return subs


def update(db: Session, sub_id, payload: SubtitleUpdate) -> Subtitle:
    sub = db.get(Subtitle, sub_id)
    if not sub:
        raise ValueError("字幕不存在")
    if payload.text is not None:
        sub.text = payload.text
    if payload.start_ms is not None:
        sub.start_ms = payload.start_ms
    if payload.end_ms is not None:
        sub.end_ms = payload.end_ms
    db.commit()
    db.refresh(sub)
    return sub
