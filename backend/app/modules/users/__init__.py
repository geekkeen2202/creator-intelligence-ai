from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.repository import UserRepository
from app.modules.users.router import router as users_router

__all__ = ["users_router", "list_all_user_emails"]


async def list_all_user_emails(db: AsyncSession) -> list[tuple[UUID, str]]:
    """Public interface for background jobs (e.g. weekly briefing) needing
    every user — (user_id, email) pairs only, not the full ORM row."""
    users = await UserRepository(db).list_all()
    return [(u.id, u.email) for u in users]
