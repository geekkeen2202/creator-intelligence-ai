from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class Channel(BaseModelMixin, Base):
    __tablename__ = "channels"

    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="youtube")
    external_channel_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    handle: Mapped[str | None] = mapped_column(String(128), default=None)
    # Cumulative snapshot from the last analytics sync (subscriberCount,
    # viewCount, ...) — not a time series; analytics_daily is that.
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Pointer cache only — the actual profile lives in voice_profiles
    # (append-only versions). See ARCHITECTURE.md §7.
    #
    # Deliberately NOT duplicating niche/language columns here even though
    # TechnicalDesign.md §3.1 lists them on `channels` — they already live in
    # trending.ChannelNicheAssignment (the trending module's own view, kept
    # in sync via CHANNEL_ANALYZED, ARCHITECTURE.md §4 rule 2b) and adding a
    # second copy here would just be a sync-drift bug waiting to happen.
    current_voice_profile_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_profiles.id"), default=None
    )
