"""segment.enhanced_target 扩大为 VARCHAR(64)（提示词缓存键纳入 ref_labels 指纹）。

2026-08-08：关键帧参考图增强 prompt 缓存键追加 ref_labels 指纹
（如 image_bilingual_r8d16f878，21 字符），原 String(16) 写入超长报错
（StringDataRightTruncation）→ 分镜重新生成关键帧 500 无响应。

Revision ID: 0030
Revises: 0029
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("segment", "enhanced_target", type_=sa.String(64))


def downgrade() -> None:
    op.alter_column("segment", "enhanced_target", type_=sa.String(16))
