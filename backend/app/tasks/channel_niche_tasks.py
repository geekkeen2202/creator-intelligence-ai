import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.modules import voice_profiles
from app.modules.channels.events import CHANNEL_ANALYZED
from app.modules.trending.classifier import classify_niche
from app.modules.trending.repository import TrendingRepository
from app.shared.events import subscribe
from app.tasks.celery_app import celery_app


def _extract_keywords(voice_dna: Any, _depth: int = 0) -> list[str]:
    """Pulls every string leaf out of the voice_dna payload, regardless of its
    exact shape — Voice DNA's schema is owned by another module/team and may
    still change, so this stays forward-compatible rather than assuming keys.
    """
    if _depth > 4 or voice_dna is None:
        return []
    if isinstance(voice_dna, str):
        return [voice_dna]
    if isinstance(voice_dna, dict):
        keywords: list[str] = []
        for value in voice_dna.values():
            keywords.extend(_extract_keywords(value, _depth + 1))
        return keywords
    if isinstance(voice_dna, list):
        keywords = []
        for item in voice_dna:
            keywords.extend(_extract_keywords(item, _depth + 1))
        return keywords
    return []


@subscribe(CHANNEL_ANALYZED)
@celery_app.task(
    name="app.tasks.channel_niche_tasks.assign_channel_niche", bind=True, max_retries=3
)
def assign_channel_niche(self, payload: dict) -> None:
    """Classifies a channel into a canonical niche (thin body — logic in classify_niche).

    Runs once per CHANNEL_ANALYZED event, never per trending refresh — this is
    a one-time cost per channel, so it doesn't scale with ingestion frequency
    or read traffic (see app/modules/trending/classifier.py).
    """
    try:
        asyncio.run(_assign(UUID(payload["channel_id"])))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _assign(channel_id: UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            voice_dna = await voice_profiles.get_current_profile_dict(session, channel_id)
            keywords = _extract_keywords(voice_dna)
            niche, confidence = classify_niche(keywords)
            await TrendingRepository(session).upsert_assignment(
                channel_id, niche, keywords, confidence
            )
    finally:
        await engine.dispose()
