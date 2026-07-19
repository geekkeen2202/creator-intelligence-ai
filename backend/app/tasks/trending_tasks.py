from datetime import timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.facebook_adapter import FacebookAdapter
from app.adapters.google_trends_adapter import GoogleTrendsAdapter
from app.adapters.openrouter_adapter import OpenRouterAdapter
from app.adapters.reddit_adapter import RedditAdapter
from app.adapters.x_adapter import XAdapter
from app.adapters.youtube_adapter import YouTubeAdapter
from app.adapters.youtube_trends_adapter import YouTubeTrendsAdapter
from app.config import get_settings
from app.modules.trending.events import TRENDING_COLD_NICHE_REQUESTED
from app.modules.trending.repository import TrendingRepository
from app.modules.trending.service import TrendingService
from app.shared.events import run_with_event_flush, subscribe
from app.tasks.celery_app import celery_app

log = structlog.get_logger(__name__)

NICHES = ["technology", "finance", "entertainment", "education", "gaming", "general"]
LANGUAGES = ["en", "hi"]

# Spread dispatch instead of firing all (niche, language) pairs at once — a
# burst of near-simultaneous requests from one IP is the worst pattern against
# rate-limited/unofficial sources like Google Trends (see GoogleTrendsAdapter).
_DISPATCH_STAGGER_SECONDS = 20

# How long a soft-deleted (superseded) batch sticks around before hard-delete —
# short enough to bound table growth, long enough to debug a bad refresh.
_PURGE_RETENTION = timedelta(days=7)


@celery_app.task(name="app.tasks.trending_tasks.refresh_trending_topics")
def refresh_trending_topics() -> None:
    """Celery Beat entrypoint — fans out one idempotent ingest task per (niche, language)."""
    pairs = [(niche, language) for niche in NICHES for language in LANGUAGES]
    for i, (niche, language) in enumerate(pairs):
        ingest_trending.apply_async(args=(niche, language), countdown=i * _DISPATCH_STAGGER_SECONDS)


@celery_app.task(
    name="app.tasks.trending_tasks.ingest_trending",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def ingest_trending(self, niche: str, language: str) -> None:
    """Fetches topic signals + top competitor videos (thin body — logic lives in the service)."""
    try:
        run_with_event_flush(_ingest(niche, language))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


@subscribe(TRENDING_COLD_NICHE_REQUESTED)
@celery_app.task(name="app.tasks.trending_tasks.ingest_cold_niche", bind=True, max_retries=1)
def ingest_cold_niche(self, payload: dict) -> None:
    """One-off ingest for a niche never seen before (see TrendingService._maybe_trigger_cold_start).

    Deduped upstream via a Redis lock before this was even enqueued — this
    just delegates to the same idempotent ingest_trending task.
    """
    ingest_trending.delay(payload["niche"], payload["language"])


@celery_app.task(
    name="app.tasks.trending_tasks.purge_expired_trending_data", bind=True, max_retries=3
)
def purge_expired_trending_data(self) -> None:
    """Hard-deletes soft-deleted trending rows past retention (thin body — see repository)."""
    try:
        purged = run_with_event_flush(_purge())
        log.info("trending_purge_completed", rows_deleted=purged)
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _purge() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await TrendingRepository(session).purge_soft_deleted(_PURGE_RETENTION)
    finally:
        await engine.dispose()


async def _ingest(niche: str, language: str) -> None:
    settings = get_settings()
    # Fresh engine/redis per run: asyncio.run creates a new event loop each time,
    # so the app-global pooled engine and cached Redis client cannot be reused here.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = TrendingService(
                TrendingRepository(session),
                redis,
                db=session,
                trend_sources=[
                    YouTubeTrendsAdapter(),
                    GoogleTrendsAdapter(),
                    RedditAdapter(),
                    XAdapter(),
                    FacebookAdapter(),
                ],
                video_source=YouTubeAdapter() if settings.youtube_api_key else None,
                llm=OpenRouterAdapter() if settings.openrouter_api_key else None,
            )
            await service.ingest(niche, language)
    finally:
        await redis.aclose()
        await engine.dispose()
