import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class TaskType(str, enum.Enum):
    # P0
    generate_keyframe = "generate_keyframe"
    generate_video = "generate_video"
    export_film = "export_film"
    # 剧集导出（2026-08-13）：按幕导出单集成片（幕级视频优先、逐镜回退）
    export_episode = "export_episode"
    # P1 资产
    generate_asset_cover = "generate_asset_cover"
    generate_asset_fourview = "generate_asset_fourview"
    # 场景多视角（POV 六格合一图，两阶段：cover→俯视→5POV→拼接）
    generate_scene_multiview = "generate_scene_multiview"
    # P1 TTS
    generate_voice = "generate_voice"
    # P1 批量
    batch_keyframes = "batch_keyframes"
    batch_videos = "batch_videos"
    # 视频超分（480p → 1080p，2026-08-23）：up scale 以 ComfyUI 逐帧超分 + 本地保真回封
    upscale_video = "upscale_video"
    batch_upscale_videos = "batch_upscale_videos"
    # P2 批量配音重跑
    batch_voice = "batch_voice"
    # 批量资产生成
    batch_asset_covers = "batch_asset_covers"
    # 画布(生图工作台)批量生成:遍历节点派发关键帧子任务并回写画布文档
    canvas_generate = "canvas_generate"
    # 画布导演台模式(2026-08-29):多段连续生视频(MiniMax H3 Director 工作流)
    director_generate = "director_generate"
    # 项目页签连续长片(2026-09):整集多段连续整片(AIMixer Director, 不进画布)
    project_director = "project_director"
    # P3 小说→剧本
    analyze_novel = "analyze_novel"
    adapt_script = "adapt_script"
    # P6 章节续接追加
    adapt_continuation = "adapt_continuation"
    # 2026-08-30 手工导入剧本「生成分镜」（异步；剧本自带分镜则按剧本直落）
    generate_shot_plan = "generate_shot_plan"
    # 2026-08-11 AI 长篇小说写作（Agent 触发，逐章异步生成）
    write_novel = "write_novel"
    # 2026-08-12 分集剧本写作（Agent 触发，逐集生成完整剧本并逐集追加为项目幕）
    write_script = "write_script"
    # P3 BGM / 音效
    generate_bgm = "generate_bgm"
    generate_sfx = "generate_sfx"
    # P7 幕级视频（取代逐镜视频）
    generate_episode_design = "generate_episode_design"  # 幕首/幕尾图（设计图，阶段1）
    generate_episode_video = "generate_episode_video"    # 幕级视频（阶段2，依赖首尾帧）
    # 白模故事版（2026-08-10）：动作序列多镜头完整展示（手动触发）
    generate_action_sequence_template = "generate_action_sequence_template"  # 白模模板图 + LLM 格子分组（阶段1）
    compose_action_sequence = "compose_action_sequence"  # 逐组视频 + 硬切拼接（阶段2，依赖模板图）
    # AI 视频页签（2026-08-11）：项目级独立视频草稿（文生/首尾帧/参考视频+图片混合）
    generate_video_draft = "generate_video_draft"
    # P0-1 成片评估（规则分 + LLM 四维分 + 反哺建议，对标 Higgsfield Virality Predictor）
    evaluate_episode = "evaluate_episode"
    # P2-5 多语言配音与字幕导出（翻译对白 + TTS + SRT）
    dub_episode = "dub_episode"
    # P1-4 修片工作流：画幅重切 / 换声 / 草图局部重绘
    reframe_video = "reframe_video"
    voice_change_video = "voice_change_video"
    draw_to_video = "draw_to_video"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class Task(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_project_status", "project_id", "status"),
        Index("ix_task_target", "target_type", "target_id"),
        Index("ix_task_status_created", "status", "created_at"),
        Index("ix_task_status_heartbeat", "status", "last_heartbeat_at"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="task_type"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), default=TaskStatus.pending, nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    result_url: Mapped[str | None] = mapped_column(String(512))
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_task_id: Mapped[str | None] = mapped_column(Text)
    poll_url: Mapped[str | None] = mapped_column(String(512))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # P3 任务实时性：worker 心跳，定时回收任务据此判断 running 是否卡死
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project = relationship("Project", back_populates="tasks")
