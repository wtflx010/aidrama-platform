# -*- coding: utf-8 -*-
"""
project 新增 video_params 列（项目级视频生成参数：fps/res/video_size/steps/cfg/seed/turbo）。
批量生成按项目统一出片时作为参数源；分镜级 gen_params 显式设置时优先。

Revision ID: 0074
Revises: 0073
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project", sa.Column("video_params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("project", "video_params")
