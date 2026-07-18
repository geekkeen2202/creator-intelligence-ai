from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class Script(BaseModelMixin, Base):
    __tablename__ = "scripts"

    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), index=True
    )
    topic: Mapped[str] = mapped_column(String(256))
    hook: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    cta: Mapped[str] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(default=None)

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

    script_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id"), index=True
    )
    external_video_id: Mapped[str] = mapped_column(String(32))
    matched_by: Mapped[str] = mapped_column(String(16))  # manual | auto
    ctr: Mapped[float | None] = mapped_column(Float, default=None)
    avg_view_duration: Mapped[float | None] = mapped_column(Float, default=None)
    views: Mapped[int | None] = mapped_column(Integer, default=None)
