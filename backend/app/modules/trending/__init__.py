from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trending.repository import TrendingRepository
from app.modules.trending.router import router as trending_router
from app.modules.trending.schemas import NicheContext
from app.modules.trending.service import TrendingService

__all__ = ["trending_router", "NicheContext", "get_niche_context", "get_channel_context"]


async def get_niche_context(
    db: AsyncSession, redis: Redis, niche: str, language: str = "en"
) -> NicheContext:
    """Public interface for other modules (e.g. scripts) needing trend + competitor context."""
    service = TrendingService(TrendingRepository(db), redis)
    return await service.get_niche_context(niche, language)


async def get_channel_context(
    db: AsyncSession, redis: Redis, channel_id: UUID, language: str = "en"
) -> NicheContext:
    """Personalized variant of get_niche_context — re-ranks the same shared
    niche batch using the channel's assigned niche/keywords. Adds zero extra
    external API calls regardless of how many channels call this.
    """
    service = TrendingService(TrendingRepository(db), redis)
    return await service.get_channel_context(channel_id, language)
