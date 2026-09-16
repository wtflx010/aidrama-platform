import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class Segment(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "segment"
    __table_args__ = (UniqueConstraint("episode_id", "index", name="uq_segment_episode_index"),)

    episode_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 2026-08-22 分镜标题（2~6 字概要，LLM 生成或导入剧本时产出，用于快速识别分镜）
    title: Mapped[str | None] = mapped_column(String(100))
    # 2026-08-22 可配置视频生成参数（三列工作台右侧面板）：
    # {cfg, steps, seed, fps, reference_src: none|keyframe|prev_tail|custom,
    #  custom_first_frame_url, res: "480p"|"720p"|"1080p"} 前端保存、生成时透传模型
    gen_params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    shot_type: Mapped[str | None] = mapped_column(String(32))
    camera: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    dialogue: Mapped[str | None] = mapped_column(Text)
    narration: Mapped[str | None] = mapped_column(Text)
    # 结构化对白（权威源）：[{"speaker":"角色名","text":"...","emotion":"愤怒","character_id":"<uuid>"}]
    # dialogue 字符串字段保留做兼容展示；dialogue_lines 为空时回退用 dialogue
    dialogue_lines: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 本镜整体氛围：平静/紧张/悲伤/温馨/愤怒/欢快/恐惧/史诗（用于旁白情绪 + BGM 参考）
    emotion: Mapped[str | None] = mapped_column(String(32))
    duration: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    character_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    scene_id: Mapped[str | None] = mapped_column(String(64))
    prop_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 九宫格构图点位(画布标注回写):top_left/top/.../center 等,见画布 COMPOSITION_POINTS
    composition_point: Mapped[str | None] = mapped_column(String(32))
    # P4 提示词增强缓存：LLM 生成的精细英文 prompt 与负面词（分镜级复用）
    enhanced_prompt: Mapped[str | None] = mapped_column(Text)
    enhanced_negative_prompt: Mapped[str | None] = mapped_column(Text)
    # 缓存对应的生成目标（image/video），避免两种模板互相覆盖；
    # 2026-08-08 改为 String(64)：缓存键纳入 ref_labels 指纹（如 image_bilingual_r8d16f878）
    enhanced_target: Mapped[str | None] = mapped_column(String(64))
    # 2026-08-10 动作序列标记：打斗/动作段连续分镜打同一标记（如 "as_1"），
    # 用于聚合生成白模模板图 + 逐组视频 + 拼接完整动作视频；空串=普通叙事镜头
    action_sequence: Mapped[str | None] = mapped_column(String(64))
    # 2026-08-28 分镜内多镜头运镜节拍：结构化时间分段镜头计划。
    # [{start_sec, end_sec, shot_type, camera, content}]，时间连续覆盖 0~duration；
    # 空列表 = 整镜单镜头（沿用 shot_type/camera 单值）；2 拍及以上驱动增强层
    # 生成 H3 官方 [Shot N] At MM:SS.mmm 时间码切镜（见 services/shot_beats.py）
    shot_beats: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    episode = relationship("Episode", back_populates="segments")
    keyframes = relationship("Keyframe", back_populates="segment", cascade="all, delete-orphan")
    videos = relationship("VideoClip", back_populates="segment", cascade="all, delete-orphan")
    voice_lines = relationship("VoiceLine", back_populates="segment", cascade="all, delete-orphan")
    subtitles = relationship("Subtitle", back_populates="segment", cascade="all, delete-orphan")
