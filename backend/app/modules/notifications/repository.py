from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import NotificationLog


class NotificationRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def log(
        self, *, user_id: UUID, template_name: str, status: str = "sent"
    ) -> NotificationLog:
        entry = NotificationLog(user_id=user_id, template_name=template_name, status=status)
        self._db.add(entry)
        await self._db.commit()
        await self._db.refresh(entry)
        return entry
