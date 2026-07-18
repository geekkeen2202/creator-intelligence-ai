from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TrendingTopicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    niche: str
    language: str
    source: str
    title: str
    summary: str
    score: float
    expires_at: datetime


class VideoContext(BaseModel):
    """Structured LLM output distilled from a video's public metadata (§7: never parse raw text)."""

    summary: str
    key_points: list[str] = Field(default_factory=list)
    hook_style: str = ""
    angle: str = ""


class TrendingVideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    niche: str
    language: str
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    url: str
    published_at: datetime | None
    stats: dict
    summary: str
    context: dict
    score: float
    expires_at: datetime


class NicheContext(BaseModel):
    """Bundle consumed by the scripts module when generating a script."""

    niche: str
    language: str
    topics: list[TrendingTopicRead]
    videos: list[TrendingVideoRead]
