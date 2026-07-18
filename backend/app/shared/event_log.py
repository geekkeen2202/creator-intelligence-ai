"""Persisted append-only audit log for every emitted event (ARCHITECTURE.md §5).

Lives alongside the event bus itself (shared/events.py) — infrastructure
concern, not a feature module, so it doesn't follow the modules/<feature>/
vertical-slice layout.
"""

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class EventLog(BaseModelMixin, Base):
    __tablename__ = "events"

    event_name: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
