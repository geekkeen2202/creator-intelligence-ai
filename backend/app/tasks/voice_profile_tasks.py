from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.whisper_adapter import WhisperAdapter
from app.adapters.youtube_adapter import YouTubeAdapter
from app.config import get_settings
from app.modules import channels
from app.modules.voice_profiles.repository import VoiceProfileRepository
from app.modules.voice_profiles.service import VoiceProfileService
from app.shared.events import CHANNEL_ANALYZED, emit, run_with_event_flush
from app.tasks.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="app.tasks.voice_profile_tasks.transcribe_video", bind=True, max_retries=2)
def transcribe_video(self, video_id: str) -> None:
    """One video's transcription — swallows its own failures so a single bad
    video never breaks the chord it's part of (ARCHITECTURE.md §11 fan-out
    isolation: the chain completes with the minimum viable corpus).
    """
    try:
        run_with_event_flush(_transcribe(UUID(video_id)))
    except Exception as exc:
        log.warning("transcribe_video_failed", video_id=video_id, error=str(exc))


async def _transcribe(video_id: UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = VoiceProfileService(
                VoiceProfileRepository(session),
                session,
                redis,
                social=YouTubeAdapter(),
                transcription=WhisperAdapter() if settings.openai_api_key else None,
            )
            await service.transcribe_video(video_id)
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(
    name="app.tasks.voice_profile_tasks.extract_voice_profile", bind=True, max_retries=3
)
def extract_voice_profile(self, channel_id: str) -> None:
    """Chord callback — runs once per ingest_channel batch, after every
    transcribe_video task in the fan-out has completed (each always
    succeeds from Celery's perspective; see transcribe_video above).
    """
    try:
        run_with_event_flush(_extract(UUID(channel_id)))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _extract(channel_id: UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            repository = VoiceProfileRepository(session)
            is_first_version = await repository.get_latest_version_number(channel_id) == 0
            service = VoiceProfileService(repository, session, redis, social=YouTubeAdapter())
            await service.extract_voice_profile(channel_id)
            if is_first_version:
                # Preserves the existing channel.analyzed contract (e.g.
                # assign_channel_niche subscribes to it) — fired once, at
                # first successful onboarding, not on every later refinement.
                emit(CHANNEL_ANALYZED, {"channel_id": str(channel_id)})
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="app.tasks.voice_profile_tasks.refine_voice_profiles", bind=True)
def refine_voice_profiles(self) -> None:
    """Celery Beat entrypoint (TechnicalDesign.md §5.3 M6) — runs weekly per
    active creator; each channel's refinement is independent so one failure
    never blocks the rest.
    """
    try:
        run_with_event_flush(_refine_all())
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _refine_all() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            channel_ids = await channels.list_all_channel_ids(session)
            for channel_id, _external_channel_id in channel_ids:
                try:
                    repository = VoiceProfileRepository(session)
                    service = VoiceProfileService(
                        repository, session, redis, social=YouTubeAdapter()
                    )
                    refined = await service.refine_profile(channel_id)
                    if refined:
                        log.info("voice_profile_refined", channel_id=str(channel_id))
                except Exception as exc:
                    log.warning(
                        "voice_profile_refinement_failed",
                        channel_id=str(channel_id),
                        error=str(exc),
                    )
    finally:
        await redis.aclose()
        await engine.dispose()
