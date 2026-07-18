from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.models import AnalyticsDaily


class AnalyticsRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def list_for_channel(
        self, channel_id: UUID, since: date | None = None
    ) -> list[AnalyticsDaily]:
        stmt = select(AnalyticsDaily).where(AnalyticsDaily.channel_id == channel_id)
        if since is not None:
            stmt = stmt.where(AnalyticsDaily.day >= since)
        result = await self._db.execute(stmt.order_by(AnalyticsDaily.day.desc()))
        return list(result.scalars().all())

    async def upsert_day(
        self,
        channel_id: UUID,
        day: date,
        *,
        views: int,
        watch_time_minutes: int,
        subscribers_gained: int,
    ) -> AnalyticsDaily:
        stmt = (
            insert(AnalyticsDaily)
            .values(
                channel_id=channel_id,
                day=day,
                views=views,
                watch_time_minutes=watch_time_minutes,
                subscribers_gained=subscribers_gained,
            )
            .on_conflict_do_update(
                index_elements=[AnalyticsDaily.channel_id, AnalyticsDaily.day],
                set_={
                    "views": views,
                    "watch_time_minutes": watch_time_minutes,
                    "subscribers_gained": subscribers_gained,
                },
            )
            .returning(AnalyticsDaily)
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        return result.scalar_one()
