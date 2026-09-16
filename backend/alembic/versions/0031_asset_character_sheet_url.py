"""asset 表新增 character_sheet_url 列（四格合一四视图）。

2026-08-09：四视图生成流程切换为封面驱动的 FLUX.2 Klein 9B + CharacterSheet LoRA，
产出单张横向四格合一图（左半身特征格 + 正面/侧面/背面全身，1536×1024），
存 asset.character_sheet_url（R2V 参考图规格）。旧 four_view_urls 四张独立分图
方案仅对历史数据保留，新链路不再写入。

Revision ID: 0031
Revises: 0030
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("character_sheet_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "character_sheet_url")
