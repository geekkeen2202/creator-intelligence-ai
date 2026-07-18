from uuid import UUID

from pydantic import BaseModel, ConfigDict


class VoiceProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    version: int
    profile: dict
    confidence: dict
