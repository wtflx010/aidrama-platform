import enum
import uuid

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class ProjectStatus(str, enum.Enum):
    draft = "draft"
    generating = "generating"
    done = "done"
    failed = "failed"


class Project(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "project"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), default="local", nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text)
    script: Mapped[str | None] = mapped_column(Text)
    # 风格：外键关联 art_style（预设风格）；为空时用 art_style_prompt（自定义）
    style_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("art_style.id", ondelete="SET NULL")
    )
    # 自定义风格文本（style_id 为空时使用，注入生图 prompt）
    art_style_prompt: Mapped[str | None] = mapped_column(Text)
    # 项目级创作规则（世界观/角色/文风等约束，@项目时注入对话上下文）
    rules: Mapped[str | None] = mapped_column(Text)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9", nullable=False)
    resolution: Mapped[str] = mapped_column(String(16), default="HD", nullable=False)
    # 项目级视频生成参数（2026-08-23）：fps/res/video_size/steps/cfg/seed/turbo。
    # 批量生成等「按项目统一出片」时作为参数源；分镜级 gen_params 显式设置时优先。
    # 与 Segment.gen_params 同构，便于「分镜级 > 项目级 > 默认」三级解析。
    video_params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"),
        default=ProjectStatus.draft,
        nullable=False,
    )
    cover_url: Mapped[str | None] = mapped_column(String(512))
    # 项目级旁白声线档案（结构同 Asset.voice_profile）
    narrator_profile: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # P6 章节续接追加：项目来源小说 + 已改编到的章节号（续接断点，防乱序/重复追加）
    source_novel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("novel.id", ondelete="SET NULL")
    )
    processed_upto_chapter: Mapped[int | None] = mapped_column(Integer)

    art_style = relationship("ArtStyle")
    episodes = relationship("Episode", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    # 全局资产库绑定：项目可复用全局资产（资产不随项目删除而删除，仅解绑）
    assets_bound = relationship(
        "ProjectAsset", back_populates="project", cascade="all, delete-orphan",
    )
    # 全局音频库绑定：项目可复用全局 BGM（不随项目删除而删除，仅解绑）
    bgms_bound = relationship(
        "ProjectBgm", back_populates="project", cascade="all, delete-orphan",
    )


class Episode(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "episode"
    __table_args__ = (UniqueConstraint("project_id", "index", name="uq_episode_project_index"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="主幕", nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    # P7 幕级视频：LLM 生成的幕级时间轴分镜描述（整幕）+ 幕级视频整体状态
    video_script: Mapped[str | None] = mapped_column(Text)
    video_status: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    # 项目页签「连续长片」(2026-09)：AIMixer 导演台多段连续整片产物（不含画布）
    continuous_film_url: Mapped[str | None] = mapped_column(String(512))
    continuous_film_duration: Mapped[float | None] = mapped_column(Float)
    continuous_film_meta: Mapped[dict | None] = mapped_column(JSONB)
    continuous_film_status: Mapped[str] = mapped_column(String(16), default="none", nullable=False)

    project = relationship("Project", back_populates="episodes")
    segments = relationship("Segment", back_populates="episode", cascade="all, delete-orphan")
    episode_videos = relationship(
        "EpisodeVideo", back_populates="episode", cascade="all, delete-orphan"
    )
    action_sequences = relationship(
        "ActionSequence", back_populates="episode", cascade="all, delete-orphan"
    )
    episode_evals = relationship(
        "EpisodeEval", back_populates="episode", cascade="all, delete-orphan"
    )
