from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.models import Channel


class ChannelRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_id(self, channel_id: UUID) -> Channel | None:
        return await self._db.get(Channel, channel_id)

    async def list_by_user(self, user_id: UUID) -> list[Channel]:
        result = await self._db.execute(select(Channel).where(Channel.user_id == user_id))
        return list(result.scalars().all())

    async def list_all(self) -> list[Channel]:
        result = await self._db.execute(select(Channel).where(Channel.deleted_at.is_(None)))
        return list(result.scalars().all())

    async def create(
        self,
        *,
        user_id: UUID,
        platform: str,
        external_channel_id: str,
        title: str,
        handle: str | None = None,
    ) -> Channel:
        channel = Channel(
            user_id=user_id,
            platform=platform,
            external_channel_id=external_channel_id,
            title=title,
            handle=handle,
        )
        self._db.add(channel)
        await self._db.commit()
        await self._db.refresh(channel)
        return channel

    async def set_current_voice_profile(self, channel_id: UUID, voice_profile_id: UUID) -> None:
        channel = await self.get_by_id(channel_id)
        if channel is not None:
            channel.current_voice_profile_id = voice_profile_id
            await self._db.commit()

    async def update_stats(self, channel_id: UUID, stats: dict) -> None:
        channel = await self.get_by_id(channel_id)
        if channel is not None:
            channel.stats = stats
            await self._db.commit()
