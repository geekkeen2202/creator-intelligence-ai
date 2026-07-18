from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ThumbnailGenerateRequest(BaseModel):
    script_id: UUID


class ThumbnailBriefRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    script_id: UUID
    brief: dict
