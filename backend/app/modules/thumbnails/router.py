from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.thumbnails.repository import ThumbnailRepository
from app.modules.thumbnails.schemas import ThumbnailBriefRead, ThumbnailGenerateRequest
from app.modules.thumbnails.service import (
    ThumbnailGenerationFailedError,
    ThumbnailScriptNotFoundError,
    ThumbnailService,
)
from app.shared.cache import get_redis
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/thumbnails", tags=["thumbnails"])


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ThumbnailService:
    return ThumbnailService(ThumbnailRepository(db), db, redis)


@router.post("", response_model=ThumbnailBriefRead, status_code=status.HTTP_201_CREATED)
async def generate_thumbnail_brief(
    body: ThumbnailGenerateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ThumbnailService, Depends(get_service)],
):
    try:
        return await service.generate(user_id=UUID(user.user_id), script_id=body.script_id)
    except ThumbnailScriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ThumbnailGenerationFailedError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
