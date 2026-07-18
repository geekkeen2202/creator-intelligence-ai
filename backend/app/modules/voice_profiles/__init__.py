from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.voice_profiles import service
from app.modules.voice_profiles.router import router as voice_profiles_router

__all__ = [
    "voice_profiles_router",
    "get_prompt_block",
    "get_current_profile_version",
    "get_current_profile_dict",
]


async def get_prompt_block(db: AsyncSession, redis: Redis, channel_id: UUID) -> str:
    """Public interface for other modules (e.g. scripts, thumbnails) needing
    the formatted Voice DNA block for prompt-building."""
    return await service.get_prompt_block(db, redis, channel_id)


async def get_current_profile_dict(db: AsyncSession, channel_id: UUID) -> dict | None:
    """Public interface for other modules (e.g. niche classification) needing
    the raw profile payload rather than the formatted prompt block."""
    voice_profile = await service.get_current(db, channel_id)
    return voice_profile.profile if voice_profile else None


async def get_current_profile_version(db: AsyncSession, channel_id: UUID) -> int | None:
    """Public interface for other modules needing the current profile
    version to stamp on provenance columns (ARCHITECTURE.md §8 rule 9)."""
    return await service.get_current_profile_version(db, channel_id)
