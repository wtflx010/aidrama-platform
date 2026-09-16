"""清空全部分镜的提示词增强缓存（P4.2 电影级提示词模板升级）。

旧缓存基于旧模板生成（无电影级布光/微动态/环境音/成片质感要求）。
升级模板后旧缓存失效，需置空让后续关键帧/视频生成重新走 LLM 增强。

Revision ID: 0018
Revises: 0017
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE segment SET enhanced_prompt = NULL, "
            "enhanced_negative_prompt = NULL, enhanced_target = NULL"
        )
    )


def downgrade() -> None:
    # 缓存数据不可逆，回滚不还原（下次生成会自动重建），留空由人工处理。
    pass
