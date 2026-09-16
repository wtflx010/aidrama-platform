"""美术风格预设模型：项目创建时选择，注入全链路生图 prompt。

- name：风格名称（中文展示）
- category：分类（写实/动漫/国风/科幻/插画/3D/复古/单色）
- prompt_fragment：英文 prompt 片段，拼接到生图 prompt
- description：中文描述
- cover_url：风格封面图 URL
- reference_images：参考图 URL 列表
- sort_order：排序
- is_builtin：内置预设标记（不可删除）
"""
import enum
import uuid

from sqlalchemy import Boolean, Enum, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class ArtStyle(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "art_style"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    reference_images: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
