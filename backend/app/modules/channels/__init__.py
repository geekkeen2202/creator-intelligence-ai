from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.repository import ChannelRepository
from app.modules.channels.router import router as channels_router

__all__ = [
    "channels_router",
    "get_current_voice_profile_id",
    "set_current_voice_profile_id",
    "list_all_channel_ids",
    "update_channel_stats",
    "get_external_channel_id",
    "verify_ownership",
    "get_owner_user_id",
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


async def update_channel_stats(db: AsyncSession, channel_id: UUID, stats: dict) -> None:
    """Public interface for analytics sync to write the cumulative stats
    snapshot (TechnicalDesign.md §3.1) onto the channel row."""
    await ChannelRepository(db).update_stats(channel_id, stats)


async def get_external_channel_id(db: AsyncSession, channel_id: UUID) -> str | None:
    """Public interface for background jobs (e.g. auto outcome-matching)
    needing a single channel's external id."""
    channel = await ChannelRepository(db).get_by_id(channel_id)
    return channel.external_channel_id if channel else None


async def verify_ownership(db: AsyncSession, channel_id: UUID, user_id: UUID) -> bool:
    """Public interface for other modules (analytics, voice_profiles,
    trending) to enforce ownership on channel-scoped reads — channel
    ownership is channels' own data, so callers ask rather than duplicate
    the check (ARCHITECTURE.md §4 rule 7, §8 rule 4 — no RLS, so app-layer
    ownership checks are mandatory)."""
    channel = await ChannelRepository(db).get_by_id(channel_id)
    return channel is not None and channel.user_id == user_id


async def get_owner_user_id(db: AsyncSession, channel_id: UUID) -> UUID | None:
    """Public interface for background jobs (e.g. Whisper cost metering)
    needing the user a channel belongs to, to attribute usage correctly."""
    channel = await ChannelRepository(db).get_by_id(channel_id)
    return channel.user_id if channel else None
