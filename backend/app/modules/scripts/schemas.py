from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScriptGenerateRequest(BaseModel):
    channel_id: UUID
    topic: str
    topic_id: UUID | None = None
    language: str = "en"
    platform: str = "youtube_long"
    premium: bool = False


class ScriptRateRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    # Which part felt off (TechnicalDesign.md §7 scripts scope), e.g.
    # {"hook": false, "body": true, "cta": false} — freeform, frontend-defined.
    detail: dict | None = None


class ScriptFinalTextRequest(BaseModel):
    final_text: str


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
    topic_id: UUID | None = None
    topic: str
    language: str
    platform: str
    hook: str
    body: str
    cta: str
    b_roll_suggestions: list[str] = []
    power_word_spans: list[str] = []
    duration_estimate_seconds: float | None = None
    rating: int | None = None
    rating_detail: dict | None = None
    final_text: str | None = None


class ScriptOwnedRead(BaseModel):
    """Cross-module read for other modules (e.g. thumbnails) — ownership
    already checked by get_script_for_owner before this is returned."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    topic: str
    hook: str
    created_at: datetime


class VoiceProfileRatingSummary(BaseModel):
    """One row of the M6 measurement query (TechnicalDesign.md §6.3) —
    ratings for a channel's scripts grouped by which Voice DNA version
    generated them."""

    voice_profile_version: int | None
    rated_count: int
    avg_rating: float | None


class ScriptOutcomeSignal(BaseModel):
    """Cross-module read for voice_profiles' refinement job (§5.3) — a
    published script's hook and the view count it earned, a performance
    signal distinct from creator ratings/edits."""

    hook: str
    views: int


class ScriptFeedbackRead(BaseModel):
    """Cross-module read for voice_profiles' refinement job (§5.3) — the
    signals it needs (rating, rating_detail, generated-vs-final diff) without
    reaching into scripts' repository directly."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    hook: str
    body: str
    cta: str
    rating: int | None = None
    rating_detail: dict | None = None
    final_text: str | None = None
