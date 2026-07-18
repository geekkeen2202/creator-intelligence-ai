from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class ThumbnailBrief(BaseModelMixin, Base):
    """Text brief only — no image generation for MVP (ARCHITECTURE.md §14)."""

    __tablename__ = "thumbnail_briefs"

    script_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id"), index=True
    )
    brief: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Provenance — same rationale as scripts (§8 rule 9).
    agent_name: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
