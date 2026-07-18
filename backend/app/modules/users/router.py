from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserRead
from app.modules.users.service import UserService
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/users", tags=["users"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> UserService:
    return UserService(UserRepository(db))


@router.get("/me", response_model=UserRead)
async def get_me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[UserService, Depends(get_service)],
):
    return await service.sync_from_auth(UUID(user.user_id), user.email or "")
