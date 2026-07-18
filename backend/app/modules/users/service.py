from uuid import UUID

from app.modules.users.events import USER_REGISTERED
from app.modules.users.repository import UserRepository
from app.shared.events import emit


class UserService:
    def __init__(self, repository: UserRepository):
        self._repository = repository

    async def sync_from_auth(self, user_id: UUID, email: str):
        user, created = await self._repository.get_or_create(user_id, email)
        if created:
            emit(USER_REGISTERED, {"user_id": str(user_id), "email": email})
        return user
