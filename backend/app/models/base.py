import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    # 默认值由 Python 端生成（微秒精度）：SQLite/部分数据库 func.now() 精度仅到秒，
    # 同一秒内连续插入的多条记录 created_at 相同会令排序不稳定（历史顺序错乱 → 上下文污染）。
    # server_default 仅兜底（原生 SQL 直插等场景），格式统一为 UTC naive，与旧数据同域兼容。
    # 用 now(timezone.utc).replace(tzinfo=None) 替代已废弃的 utcnow()，语义不变（naive UTC）。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )
