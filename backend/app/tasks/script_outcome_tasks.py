from datetime import datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.youtube_adapter import YouTubeAdapter
from app.config import get_settings
from app.modules import channels, scripts
from app.modules.analytics.events import ANALYTICS_SYNCED
from app.modules.scripts.events import SCRIPT_OUTCOME_LINKED, SCRIPT_PUBLISHED
from app.modules.scripts.repository import ScriptRepository
from app.modules.trending.classifier import tokenize
from app.shared.events import emit, run_with_event_flush, subscribe
from app.tasks.celery_app import celery_app

log = structlog.get_logger(__name__)

# Auto-matching (TechnicalDesign.md §5.5): a script generated close in time
# to a video's publish date, whose topic/hook shares enough vocabulary with
# the video's title, is treated as "this is probably that script." Both
# bounds exist to avoid false positives — recency alone or overlap alone
# isn't enough signal.
_PUBLISH_WINDOW = timedelta(days=14)
_MIN_TOKEN_OVERLAP = 2


@subscribe(SCRIPT_PUBLISHED)
@celery_app.task(
    name="app.tasks.script_outcome_tasks.link_script_outcome", bind=True, max_retries=3
)
def link_script_outcome(self, payload: dict) -> None:
    """Post-publish loop (ARCHITECTURE.md §1/§11): manual linkage — matches a
    published video back to its originating script and records its current
    stats.
    """
    try:
        run_with_event_flush(_link(UUID(payload["script_id"]), payload["external_video_id"]))
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


@subscribe(ANALYTICS_SYNCED)
@celery_app.task(
    name="app.tasks.script_outcome_tasks.auto_link_script_outcomes", bind=True, max_retries=3
)
def auto_link_script_outcomes(self, payload: dict) -> None:
    """Post-publish loop (TechnicalDesign.md §5.5): auto-matching path — runs
    after each analytics sync, pairing unmatched scripts on the channel to
    recently published videos by title/topic overlap + publish window.
    """
    try:
        run_with_event_flush(_auto_link(UUID(payload["channel_id"])))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _auto_link(channel_id: UUID) -> None:
    settings = get_settings()
    if not settings.youtube_api_key:
        return

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            unmatched = await scripts.list_unmatched_for_channel(session, channel_id)
            if not unmatched:
                return

            external_channel_id = await channels.get_external_channel_id(session, channel_id)
            if external_channel_id is None:
                return

            youtube = YouTubeAdapter()
            recent_videos = await youtube.get_recent_videos(external_channel_id, limit=25)
            repository = ScriptRepository(session)
            used_video_ids: set[str] = set()

            for script in unmatched:
                script_tokens = tokenize(f"{script.topic} {script.hook}")
                best_video_id: str | None = None
                best_overlap = _MIN_TOKEN_OVERLAP - 1

                for item in recent_videos:
                    video_id = item.get("id", {}).get("videoId", "")
                    if not video_id or video_id in used_video_ids:
                        continue
                    published_raw = item.get("snippet", {}).get("publishedAt", "")
                    if not published_raw:
                        continue
                    published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                    if abs(published_at - script.created_at) > _PUBLISH_WINDOW:
                        continue
                    title_tokens = tokenize(item.get("snippet", {}).get("title", ""))
                    overlap = len(script_tokens & title_tokens)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_video_id = video_id

                if best_video_id is None:
                    continue
                if await repository.has_outcome_for_video(best_video_id):
                    continue

                used_video_ids.add(best_video_id)
                details = await youtube.get_videos_stats([best_video_id])
                views = int(details[0]["statistics"].get("viewCount", 0)) if details else None
                await repository.create_outcome(
                    script_id=script.id,
                    external_video_id=best_video_id,
                    matched_by="auto",
                    ctr=None,
                    avg_view_duration=None,
                    views=views,
                )
                emit(SCRIPT_OUTCOME_LINKED, {"script_id": str(script.id)})
    finally:
        await engine.dispose()
