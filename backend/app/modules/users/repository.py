from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self._db.get(User, user_id)

    async def list_all(self) -> list[User]:
        result = await self._db.execute(select(User).where(User.deleted_at.is_(None)))
        return list(result.scalars().all())

    async def get_or_create(self, user_id: UUID, email: str) -> tuple[User, bool]:
        existing = await self.get_by_id(user_id)
        if existing is not None:
            return existing, False

        # ON CONFLICT DO NOTHING closes the race between the check above and
        # this insert — two concurrent first-requests for the same brand-new
        # user no longer produce an IntegrityError on whichever loses the race.
        stmt = (
            insert(User)
            .values(id=user_id, email=email)
            .on_conflict_do_nothing(index_elements=[User.id])
            .returning(User)
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        row = result.scalar_one_or_none()
        if row is not None:
            return row, True
        # Lost the race — the other request's insert won; fetch what it wrote.
        return await self.get_by_id(user_id), False
