"""美术资产模型：角色 / 场景 / 道具统一表，用 type 区分。

- 角色（character）：含四视图 four_view_urls[4] + states，是关键帧一致性的参考锚点
- 场景（scene）/ 道具（prop）：结构同角色，不填 four_view_urls/states
"""
import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin
from app.models.media import MediaStatus


class AssetType(str, enum.Enum):
    character = "character"
    scene = "scene"
    prop = "prop"


class Asset(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "asset"
    __table_args__ = (
        Index("ix_asset_type", "type"),
        # 项目级资产唯一约束（迁移 0008 调整为 NOT NULL 后保留）：
        #   ix_asset_project_unique (lower(name), type, project_id)
        # SQLAlchemy 不直接支持 partial unique index 声明，故不在此处定义
    )

    # 资产所属项目（可为空 = 全局资产）；项目删除时置空并解绑（资产保留在全局库）。
    # 2026-08-22 全局资产库改造：资产可被多个项目使用（关联表 project_assets），
    # 此列保留为「创建/归属项目」便于兼容旧查询；新增项目可通过绑定关系复用。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="asset_type"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    # 四格合一四视图（CharacterSheet LoRA 封面驱动，2026-08-09）：单张横向四格图
    # （左半身特征格 + 正面/侧面/背面全身），R2V 参考图规格（1536×1024）。
    # 替代旧的 four_view_urls 四张独立分图方案（新链路只生成该字段）。
    character_sheet_url: Mapped[str | None] = mapped_column(String(512), default=None)
    # 仅 scene 填：六格合一场景多视角图（POV 人物视角，2026-08-10）。
    # 两阶段生成：阶段①场景cover→俯视图（布局锚点）；阶段②俯视图→5个POV视角→3x2拼接。
    # 视角：俯视锚点 + 街口正面/回望/左侧/右侧/远景全景。R2V/关键帧参考图规格。
    scene_sheet_url: Mapped[str | None] = mapped_column(String(512), default=None)
    # 场景多视角机位组（2026-08-18 v5）：list[{"name","view_text"}] 六格渲染格式，
    # GUI 用户配置覆盖（优先于导演 LLM 推断与默认机位组）。仅 scene 填。
    scene_shots: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 仅 character 填：[正面url, 侧面url, 背面url, 全身url]
    four_view_urls: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 仅 character 填：[{state_name, image_url}]
    states: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    reference_images: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # 多版本：[{version, url, created_at}]
    art_versions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expanded_description: Mapped[str | None] = mapped_column(Text)
    # 角色声线档案（仅 character 填）：
    # {
    #   "gender": "male|female|neutral",
    #   "age_group": "child|youth|middle|elder",
    #   "timbre_tags": ["低沉","温和"...],
    #   "reference_audio_url": "<参考音频url>",
    #   "reference_audio_text": "<参考音频对应文本，CosyVoice zero_shot 必需>",
    #   "default_emotion": "平静",
    #   "voice_description": "LLM 生成的声线描述"
    # }
    voice_profile: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # 2026-08-23（方案A）：生成该资产时的「项目生效风格」指纹 {style_id, style_prompt}，
    # 供全局资产库跨项目复用时做风格一致性判断（风格不符不串用）。
    style_fingerprint: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False, server_default=text("'{}'::jsonb")
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    # 最近一次封面/四视图/场景图任务
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)

    project = relationship("Project", back_populates="assets")
    # 2026-08-22 全局资产库：资产 ↔ 项目的多对多绑定（删除项目时解绑，资产保留）
    projects_bound = relationship(
        "ProjectAsset", back_populates="asset", cascade="all, delete-orphan",
    )


class ProjectAsset(UUIDPkMixin, TimestampMixin, Base):
    """资产 ↔ 项目 绑定（全局资产库）：一个资产可被多个项目使用。

    项目删除时仅删除绑定行；资产本身保留在全局库。
    """
    __tablename__ = "project_asset"
    __table_args__ = (
        UniqueConstraint("asset_id", "project_id", name="uq_project_asset"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("asset.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )

    asset = relationship("Asset", back_populates="projects_bound")
    project = relationship("Project", back_populates="assets_bound")
