from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class AnalyticsDaily(BaseModelMixin, Base):
    """Time-series table — see ARCHITECTURE.md §8 (not yet partitioned; plain
    table today, revisit if/when analytics_daily volume warrants it)."""

    __tablename__ = "analytics_daily"
    __table_args__ = (UniqueConstraint("channel_id", "day", name="uq_analytics_daily_channel_day"),)

    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), index=True
    )
    day: Mapped[date] = mapped_column(Date, index=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    watch_time_minutes: Mapped[int] = mapped_column(Integer, default=0)
    subscribers_gained: Mapped[int] = mapped_column(Integer, default=0)
