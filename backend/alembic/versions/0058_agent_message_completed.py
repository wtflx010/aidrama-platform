"""agent_message 增加 completed 字段：流式增量落库标记（5.13 切走回放修复）。

assistant 消息边生成边入库（含思考过程）时 completed=False，
正常完成（或兜底保存完成）置 True；前端据此展示「思考中」并轮询直至完成。

Revision ID: 0058
Revises: 0057
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_message",
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("agent_message", "completed")
