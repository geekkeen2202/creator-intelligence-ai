import asyncio
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.youtube_adapter import YouTubeAdapter
from app.config import get_settings
from app.modules.scripts.events import SCRIPT_OUTCOME_LINKED, SCRIPT_PUBLISHED
from app.modules.scripts.repository import ScriptRepository
from app.shared.events import emit, subscribe
from app.tasks.celery_app import celery_app

log = structlog.get_logger(__name__)


@subscribe(SCRIPT_PUBLISHED)
@celery_app.task(
    name="app.tasks.script_outcome_tasks.link_script_outcome", bind=True, max_retries=3
)
def link_script_outcome(self, payload: dict) -> None:
    """Post-publish loop (ARCHITECTURE.md §1/§11): matches a published video
    back to its originating script and records its current stats.
    """
    try:
        asyncio.run(_link(UUID(payload["script_id"]), payload["external_video_id"]))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _link(script_id: UUID, external_video_id: str) -> None:
    settings = get_settings()
    if not settings.youtube_api_key:
        log.info("script_outcome_link_skipped_no_youtube_key", script_id=str(script_id))
        return

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            details = await YouTubeAdapter().get_videos_stats([external_video_id])
            # YouTube Data API v3 only exposes cumulative view counts, not
            # CTR/avg view duration — those need the YouTube Analytics API
            # (OAuth per-channel), not implemented (see analytics_tasks.py
            # for the same documented limitation).
            views = int(details[0]["statistics"].get("viewCount", 0)) if details else None

            await ScriptRepository(session).create_outcome(
                script_id=script_id,
                external_video_id=external_video_id,
                matched_by="manual",
                ctr=None,
                avg_view_duration=None,
                views=views,
            )
            emit(SCRIPT_OUTCOME_LINKED, {"script_id": str(script_id)})
    finally:
        await engine.dispose()
