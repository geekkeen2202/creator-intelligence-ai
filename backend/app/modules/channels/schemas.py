from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChannelConnectRequest(BaseModel):
    platform: str = "youtube"
    external_channel_id: str
    handle: str | None = None


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    platform: str
    external_channel_id: str
    title: str
    handle: str | None = None
    stats: dict = {}
    current_voice_profile_id: UUID | None = None


class ChannelStatusRead(BaseModel):
    """Onboarding progressive status (TechnicalDesign.md §5.1)."""

    channel_id: UUID
    videos_selected: int
    transcripts_completed: int
    voice_profile_ready: bool
    voice_profile_version: int | None = None
