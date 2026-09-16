"""task.provider_task_id 扩为 TEXT。

批量任务（batch_keyframes/batch_videos/batch_voice）把子任务 ID 数组
JSON 字符串存入 provider_task_id（形如 '["uuid1","uuid2",...]'），
整幕分镜多时长度远超 VARCHAR(128)，导致批量创建父任务时 INSERT 报
'value too long for type character varying(128)'（HTTP 500）。

Revision ID: 0019
Revises: 0018
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("task", "provider_task_id", type_=sa.Text(), existing_type=sa.String(128))


def downgrade() -> None:
    op.alter_column("task", "provider_task_id", type_=sa.String(128), existing_type=sa.Text())
