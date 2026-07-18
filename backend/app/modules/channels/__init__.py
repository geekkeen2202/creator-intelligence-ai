from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.repository import ChannelRepository
from app.modules.channels.router import router as channels_router

__all__ = [
    "channels_router",
    "get_current_voice_profile_id",
    "set_current_voice_profile_id",
    "list_all_channel_ids",
]


async def get_current_voice_profile_id(db: AsyncSession, channel_id: UUID) -> UUID | None:
    """Public interface for other modules (e.g. voice_profiles) needing the
    pointer cache — the actual profile lives in the voice_profiles table.
    """
    channel = await ChannelRepository(db).get_by_id(channel_id)
    return channel.current_voice_profile_id if channel else None


async def set_current_voice_profile_id(
    db: AsyncSession, channel_id: UUID, voice_profile_id: UUID
) -> None:
    """Public interface for voice_profiles to repoint the pointer cache after
    inserting a new append-only version (or rolling back to an earlier one).
    """
    await ChannelRepository(db).set_current_voice_profile(channel_id, voice_profile_id)


async def list_all_channel_ids(db: AsyncSession) -> list[tuple[UUID, str]]:
    """Public interface for background jobs (e.g. analytics sync) needing every
    connected channel — (channel_id, external_channel_id) pairs only, not the
    full ORM row, so callers can't reach into channels' internals."""
    channels = await ChannelRepository(db).list_all()
    return [(c.id, c.external_channel_id) for c in channels]
