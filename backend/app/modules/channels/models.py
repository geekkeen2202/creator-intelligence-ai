from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class Channel(BaseModelMixin, Base):
    __tablename__ = "channels"

    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32), default="youtube")
    external_channel_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    # Pointer cache only — the actual profile lives in voice_profiles
    # (append-only versions). See ARCHITECTURE.md §7.
    current_voice_profile_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("voice_profiles.id"), default=None
    )
