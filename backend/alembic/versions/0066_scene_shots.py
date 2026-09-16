"""场景多视角机位组：asset 表新增 scene_shots JSONB 列（POV 机位组持久化）。

Revision ID: 0066
Revises: 0065

scene_shots 存渲染格式机位组：list[{"name": str, "view_text": str}]（与默认
机位组结构一致），由用户在 GUI 配置或供任务读取覆盖默认组/导演推断。
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "asset",
        sa.Column("scene_shots", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("asset", "scene_shots")
