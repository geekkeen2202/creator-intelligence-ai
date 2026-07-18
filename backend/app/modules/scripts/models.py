from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class Script(BaseModelMixin, Base):
    __tablename__ = "scripts"

    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), index=True
    )
    # Nullable — freeform typed topics must work so generation survives any
    # trending-source outage (TechnicalDesign.md §3.2). Not a real FK
    # constraint: trending_topics rows expire/get purged (§8 purge job) while
    # a script referencing them must outlive that.
    topic_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    topic: Mapped[str] = mapped_column(String(256))
    language: Mapped[str] = mapped_column(String(16), default="en")
    platform: Mapped[str] = mapped_column(String(32), default="youtube_long")
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str] = mapped_column(Text)
    b_roll_suggestions: Mapped[list] = mapped_column(JSONB, default=list)
    power_word_spans: Mapped[list] = mapped_column(JSONB, default=list)
    duration_estimate_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    rating: Mapped[int | None] = mapped_column(default=None)
    rating_detail: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # Creator-edited version — the richest refinement signal (§5.3): diffing
    # this against hook/body/cta shows what the creator actually changed.
    final_text: Mapped[str | None] = mapped_column(Text, default=None)

    # Provenance (ARCHITECTURE.md §8 rule 9) — a script without this is a defect.
    # voice_profile_version is nullable: a channel may not have a Voice DNA
    # profile yet, and generation must still proceed (graceful fallback).
    voice_profile_version: Mapped[int | None] = mapped_column(Integer, default=None)
    agent_name: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)


class ScriptOutcome(BaseModelMixin, Base):
    """Post-publish loop — script matched back to its published video's
    performance (ARCHITECTURE.md §1/§8)."""

    __tablename__ = "script_outcomes"
    __table_args__ = (UniqueConstraint("script_id", name="uq_script_outcomes_script_id"),)

    script_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id"), index=True
    )
    external_video_id: Mapped[str] = mapped_column(String(32))
    matched_by: Mapped[str] = mapped_column(String(16))  # manual | auto
    ctr: Mapped[float | None] = mapped_column(Float, default=None)
    avg_view_duration: Mapped[float | None] = mapped_column(Float, default=None)
    views: Mapped[int | None] = mapped_column(Integer, default=None)
