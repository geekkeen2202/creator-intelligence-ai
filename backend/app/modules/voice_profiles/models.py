from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin

# Post-MVP embeddings milestone (ARCHITECTURE.md §8 rule 10, §2) — dimension
# matches OpenAI text-embedding-3-small, the likely first embedding model.
# Column stays NULL until that milestone; no retrieval code reads it before then.
_EMBEDDING_DIM = 1536


class Video(BaseModelMixin, Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("channel_id", "external_video_id", name="uq_videos_channel_external_id"),
    )

    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), index=True
    )
    external_video_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    selected_for_dna: Mapped[bool] = mapped_column(Boolean, default=False)


class Transcript(BaseModelMixin, Base):
    __tablename__ = "transcripts"

    video_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("videos.id"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # captions | whisper
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    language_detected: Mapped[str | None] = mapped_column(String(16), default=None)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    clean_text: Mapped[str] = mapped_column(Text, default="")


class TranscriptSegment(BaseModelMixin, Base):
    __tablename__ = "transcript_segments"

    transcript_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transcripts.id"), index=True
    )
    segment_type: Mapped[str] = mapped_column(String(16))  # hook | body | transition | cta
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBEDDING_DIM), default=None)


class VoiceProfile(BaseModelMixin, Base):
    """Append-only versions — never updated in place (ARCHITECTURE.md §7/§8 rule 8).
    Refinement always inserts version+1; rollback = repoint
    channels.current_voice_profile_id at an earlier version's row.
    """

    __tablename__ = "voice_profiles"
    __table_args__ = (
        UniqueConstraint("channel_id", "version", name="uq_voice_profiles_channel_version"),
    )

    channel_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    excerpt_ids: Mapped[list] = mapped_column(JSONB, default=list)
    extraction_prompt_version: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="initial")  # initial | refinement
