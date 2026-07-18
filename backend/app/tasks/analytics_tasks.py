import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.youtube_adapter import YouTubeAdapter
from app.config import get_settings
from app.modules import channels
from app.modules.analytics.repository import AnalyticsRepository
from app.modules.analytics.service import AnalyticsService
from app.tasks.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="app.tasks.analytics_tasks.sync_analytics", bind=True, max_retries=3)
def sync_analytics(self) -> None:
    """Runs daily via Celery Beat for every connected channel (thin body — logic below)."""
    try:
        asyncio.run(_sync_all())
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _sync_all() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            channel_ids = await channels.list_all_channel_ids(session)
            if not settings.youtube_api_key:
                log.info("analytics_sync_skipped_no_youtube_key", channel_count=len(channel_ids))
                return

            youtube = YouTubeAdapter()
            service = AnalyticsService(AnalyticsRepository(session))
            today = datetime.now(UTC).date()

            for channel_id, external_channel_id in channel_ids:
                try:
                    profile = await youtube.get_channel_profile(external_channel_id)
                    items = profile.get("items", [])
                    if not items:
                        continue
                    stats = items[0].get("statistics", {})
                    # YouTube Data API v3 only exposes cumulative totals, not daily
                    # deltas — true per-day watch time/engagement needs the
                    # YouTube Analytics API (OAuth per-channel), not implemented.
                    # These are cumulative snapshots, not day-over-day gains.
                    await service.record_sync(
                        channel_id,
                        today,
                        views=int(stats.get("viewCount", 0)),
                        watch_time_minutes=0,
                        subscribers_gained=int(stats.get("subscriberCount", 0)),
                    )
                except Exception as exc:
                    log.warning(
                        "channel_analytics_sync_failed", channel_id=str(channel_id), error=str(exc)
                    )
    finally:
        await engine.dispose()
