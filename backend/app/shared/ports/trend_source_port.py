from typing import Protocol

from pydantic import BaseModel, Field


class RawTrendItem(BaseModel):
    """Normalized trend item — adapters map raw platform payloads into this shape."""

    title: str
    summary: str = ""
    url: str = ""
    score: float = 0
    metadata: dict = Field(default_factory=dict)


class TrendSourcePort(Protocol):
    source: str

    def is_enabled(self) -> bool: ...

    async def fetch_trending_topics(self, niche: str, language: str) -> list[RawTrendItem]: ...
