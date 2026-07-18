import asyncio
from uuid import UUID

import structlog
from celery import chord
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.youtube_adapter import YouTubeAdapter
from app.config import get_settings
from app.modules.channels.repository import ChannelRepository
from app.modules.voice_profiles.repository import VoiceProfileRepository
from app.modules.voice_profiles.service import VoiceProfileService
from app.shared.events import CHANNEL_CONNECTED, subscribe
from app.tasks.celery_app import celery_app
from app.tasks.voice_profile_tasks import extract_voice_profile, transcribe_video

log = structlog.get_logger(__name__)


@subscribe(CHANNEL_CONNECTED)
@celery_app.task(name="app.tasks.channels_tasks.ingest_channel", bind=True, max_retries=3)
def ingest_channel(self, payload: dict) -> None:
    """Fan-out entrypoint (ARCHITECTURE.md §11): writes `videos` rows for a
    newly connected channel's recent uploads, then fans out one
    transcribe_video task per video, chained to extract_voice_profile once
    the whole batch completes (Celery chord).
    """
    try:
        video_ids = asyncio.run(_ingest(UUID(payload["channel_id"])))
    except Exception as exc:
        raise self.retry(exc=exc) from exc

    if not video_ids:
        return
    chord(transcribe_video.s(str(vid)) for vid in video_ids)(
        extract_voice_profile.si(payload["channel_id"])
    )


async def _ingest(channel_id: UUID) -> list[UUID]:
    settings = get_settings()
    if not settings.youtube_api_key:
        log.info("channel_ingest_skipped_no_youtube_key", channel_id=str(channel_id))
        return []

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            channel = await ChannelRepository(session).get_by_id(channel_id)
            if channel is None:
                return []
            service = VoiceProfileService(
                VoiceProfileRepository(session),
                session,
                redis=None,
                social=YouTubeAdapter(),
            )
            return await service.ingest_channel(channel_id, channel.external_channel_id)
    finally:
        await engine.dispose()
