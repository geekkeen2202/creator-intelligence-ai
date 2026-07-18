from uuid import UUID

from app.modules.channels.events import CHANNEL_CONNECTED
from app.modules.channels.repository import ChannelRepository
from app.shared.events import emit


class ChannelService:
    def __init__(self, repository: ChannelRepository):
        self._repository = repository

    async def connect_channel(self, *, user_id: UUID, platform: str, external_channel_id: str):
        # title is a placeholder until the analyze_channel job enriches it via SocialPlatformPort
        channel = await self._repository.create(
            user_id=user_id,
            platform=platform,
            external_channel_id=external_channel_id,
            title=external_channel_id,
        )
        emit(CHANNEL_CONNECTED, {"user_id": str(user_id), "channel_id": str(channel.id)})
        return channel

    async def list_channels(self, user_id: UUID):
        return await self._repository.list_by_user(user_id)

    async def get_channel(self, channel_id: UUID):
        return await self._repository.get_by_id(channel_id)
