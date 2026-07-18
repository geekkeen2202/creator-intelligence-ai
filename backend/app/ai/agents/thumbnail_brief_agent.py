from agno.agent import Agent
from agno.models.openrouter import OpenRouter
from pydantic import BaseModel

from app.config import get_settings

VERSION = "v1"
PROMPT_VERSION = "v1"


class ThumbnailBrief(BaseModel):
    text_overlay: str
    visual_concept: str
    emotion: str
    color_direction: str


def build_thumbnail_brief_agent() -> Agent:
    """Text brief only — no image generation for MVP (ARCHITECTURE.md §14)."""
    settings = get_settings()
    return Agent(
        name="thumbnail_brief",
        model=OpenRouter(id=settings.openrouter_model, api_key=settings.openrouter_api_key),
        description=(
            "Turns a script's hook into a text thumbnail brief (overlay text + visual concept)."
        ),
        output_schema=ThumbnailBrief,
    )
