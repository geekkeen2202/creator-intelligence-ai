from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.repository import AnalyticsRepository
from app.modules.analytics.schemas import AnalyticsDailyRead
from app.modules.analytics.service import AnalyticsService
from app.shared.database import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AnalyticsService:
    return AnalyticsService(AnalyticsRepository(db))


@router.get("/{channel_id}", response_model=list[AnalyticsDailyRead])
async def get_channel_analytics(
    channel_id: UUID,
    service: Annotated[AnalyticsService, Depends(get_service)],
    since: date | None = Query(None),
):
    return await service.get_channel_analytics(channel_id, since)
