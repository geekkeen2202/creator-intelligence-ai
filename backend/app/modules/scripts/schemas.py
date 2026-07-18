from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScriptGenerateRequest(BaseModel):
    channel_id: UUID
    topic: str
    premium: bool = False


class ScriptRateRequest(BaseModel):
    rating: int = Field(ge=1, le=5)


class ScriptPublishRequest(BaseModel):
    external_video_id: str


class ScriptOutcomeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    script_id: UUID
    external_video_id: str
    matched_by: str
    ctr: float | None = None
    avg_view_duration: float | None = None
    views: int | None = None


class ScriptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    topic: str
    hook: str
    body: str
    cta: str
    rating: int | None = None


class ScriptOwnedRead(BaseModel):
    """Cross-module read for other modules (e.g. thumbnails) — ownership
    already checked by get_script_for_owner before this is returned."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    hook: str
