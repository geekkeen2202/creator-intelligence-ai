from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class TrendingTopic(BaseModelMixin, Base):
    __tablename__ = "trending_topics"

    niche: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True, default="unknown")
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(String, default="")
    score: Mapped[float] = mapped_column(Float, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TrendingVideo(BaseModelMixin, Base):
    """Top videos of top creators in a niche — competitor intelligence for script generation."""

    __tablename__ = "trending_videos"

    niche: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16), index=True)
    video_id: Mapped[str] = mapped_column(String(32), index=True)
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    channel_title: Mapped[str] = mapped_column(String(256), default="")
    title: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(512), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    summary: Mapped[str] = mapped_column(String, default="")
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    score: Mapped[float] = mapped_column(Float, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ChannelNicheAssignment(BaseModelMixin, Base):
    """Maps a channel to its canonical niche — trending's own view of the channel,
    populated from channels.CHANNEL_ANALYZED without touching channels' internals
    (see ARCHITECTURE.md §4). Personalization reads this + the shared niche batch;
    it never triggers per-channel ingestion (see TrendingService.get_channel_context).
    """

    __tablename__ = "channel_niche_assignments"

    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), unique=True, index=True
    )
    niche: Mapped[str] = mapped_column(String(64), index=True)
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0)
