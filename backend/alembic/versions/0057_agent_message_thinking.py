"""agent_message 增加 thinking 字段：推理模型思考过程持久化（5.9 思考过程展示）。

assistant 消息保存时写入该轮模型的 reasoning_content（思考过程），
前端历史回放仍可折叠展示；不注入对话上下文。

Revision ID: 0057
Revises: 0056
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_message", sa.Column("thinking", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_message", "thinking")
