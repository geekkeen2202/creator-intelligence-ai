from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChannelConnectRequest(BaseModel):
    platform: str = "youtube"
    external_channel_id: str


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    platform: str
    external_channel_id: str
    title: str
    current_voice_profile_id: UUID | None = None
