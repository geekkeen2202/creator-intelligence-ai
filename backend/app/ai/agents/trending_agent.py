from agno.agent import Agent
from agno.models.openrouter import OpenRouter
from pydantic import BaseModel

from app.config import get_settings

VERSION = "v1"
PROMPT_VERSION = "v1"


class TrendingTopic(BaseModel):
    title: str
    summary: str
    relevance_score: float
    sources: list[str]


class TrendingResearchResult(BaseModel):
    topics: list[TrendingTopic]


def build_trending_agent() -> Agent:
    settings = get_settings()
    return Agent(
        name="trending",
        model=OpenRouter(id=settings.openrouter_model, api_key=settings.openrouter_api_key),
        description="Researches trending topics across YouTube, Google Trends and Reddit.",
        output_schema=TrendingResearchResult,
    )
