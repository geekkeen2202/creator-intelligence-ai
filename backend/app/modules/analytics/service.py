from datetime import date
from uuid import UUID

from app.modules.analytics.events import ANALYTICS_SYNCED
from app.modules.analytics.repository import AnalyticsRepository
from app.shared.events import emit


class AnalyticsService:
    def __init__(self, repository: AnalyticsRepository):
        self._repository = repository

    async def get_channel_analytics(self, channel_id: UUID, since: date | None = None):
        return await self._repository.list_for_channel(channel_id, since)

    async def record_sync(
        self,
        channel_id: UUID,
        day: date,
        *,
        views: int,
        watch_time_minutes: int,
        subscribers_gained: int,
    ):
        row = await self._repository.upsert_day(
            channel_id,
            day,
            views=views,
            watch_time_minutes=watch_time_minutes,
            subscribers_gained=subscribers_gained,
        )
        emit(ANALYTICS_SYNCED, {"channel_id": str(channel_id), "day": day.isoformat()})
        return row
