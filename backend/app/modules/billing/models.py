from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class Subscription(BaseModelMixin, Base):
    __tablename__ = "subscriptions"

    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    plan: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
    provider_subscription_id: Mapped[str] = mapped_column(String(128))


class Usage(BaseModelMixin, Base):
    """Cost metering at source (ARCHITECTURE.md §8 rule 11) — one row per
    (user, day, feature) so cost-per-script/cost-per-onboarding is queryable.
    """

    __tablename__ = "usage"
    __table_args__ = (
        UniqueConstraint("user_id", "day", "feature", name="uq_usage_user_day_feature"),
    )

    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    # script|thumbnail|whisper_minutes
    feature: Mapped[str] = mapped_column(String(32), default="script")
    scripts_generated: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
