"""drop system_metric_sample: 系统看板整链拆除后的孤儿表清理。

Revision ID: 0076_drop_system_metric_sample
Revises: 0075_video_upscale
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0076_drop_system_metric_sample"
down_revision = "0075_video_upscale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除孤儿表 system_metric_sample（系统看板功能已整体拆除，主 app 无任何读写；
    dashboard 独立工程使用内嵌 SQLite TSDB，不依赖该表）。"""
    op.drop_index("ix_system_metric_sample_node_role", table_name="system_metric_sample")
    op.drop_index("ix_system_metric_sample_ts", table_name="system_metric_sample")
    op.drop_table("system_metric_sample")


def downgrade() -> None:
    """回滚：重建 0062 原结构。"""
    op.create_table(
        "system_metric_sample",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("node_role", sa.String(10), nullable=False),
        sa.Column("hostname", sa.String(100), nullable=True),
        sa.Column("gpu_util", sa.Float(), nullable=True),
        sa.Column("gpu_temp", sa.Float(), nullable=True),
        sa.Column("gpu_power", sa.Float(), nullable=True),
        sa.Column("mem_total_gb", sa.Float(), nullable=True),
        sa.Column("mem_used_gb", sa.Float(), nullable=True),
        sa.Column("mem_util", sa.Float(), nullable=True),
        sa.Column("cpu_load", sa.Float(), nullable=True),
        sa.Column("cpu_temp", sa.Float(), nullable=True),
        sa.Column("disk_total_gb", sa.Float(), nullable=True),
        sa.Column("disk_used_gb", sa.Float(), nullable=True),
        sa.Column("link_rx_bytes", sa.BigInteger(), nullable=True),
        sa.Column("link_tx_bytes", sa.BigInteger(), nullable=True),
        sa.Column("link_rx_rate_mbps", sa.Float(), nullable=True),
        sa.Column("link_tx_rate_mbps", sa.Float(), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("kv_cache_usage", sa.Float(), nullable=True),
        sa.Column("num_running", sa.Integer(), nullable=True),
        sa.Column("num_waiting", sa.Integer(), nullable=True),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("tpot_ms", sa.Float(), nullable=True),
        sa.Column("output_tok_s", sa.Float(), nullable=True),
        sa.Column("request_success", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("generation_tokens", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_system_metric_sample_ts", "system_metric_sample", ["ts"])
    op.create_index("ix_system_metric_sample_node_role", "system_metric_sample", ["node_role"])
