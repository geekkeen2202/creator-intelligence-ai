from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import channels
from app.modules.analytics.repository import AnalyticsRepository
from app.modules.analytics.schemas import AnalyticsDailyRead
from app.modules.analytics.service import AnalyticsService
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AnalyticsService:
    return AnalyticsService(AnalyticsRepository(db))


@router.get("/{channel_id}", response_model=list[AnalyticsDailyRead])
async def get_channel_analytics(
    channel_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[AnalyticsService, Depends(get_service)],
    since: date | None = Query(None),
):
    if not await channels.verify_ownership(db, channel_id, UUID(user.user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return await service.get_channel_analytics(channel_id, since)
