from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class User(BaseModelMixin, Base):
    """Mirrors the Supabase Auth user — created lazily on first authenticated request."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256), default=None)
