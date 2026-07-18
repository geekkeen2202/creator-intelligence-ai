from agno.agent import Agent
from agno.models.openrouter import OpenRouter
from pydantic import BaseModel

from app.config import get_settings

VERSION = "v1"
PROMPT_VERSION = "v1"


class GeneratedScript(BaseModel):
    hook: str
    body: str
    cta: str


def build_script_agent() -> Agent:
    """Single-agent script generation — free tier."""
    settings = get_settings()
    return Agent(
        name="script",
        model=OpenRouter(id=settings.openrouter_model, api_key=settings.openrouter_api_key),
        description="Writes a short-form video script in the creator's Voice DNA.",
        output_schema=GeneratedScript,
        markdown=False,
    )


def build_script_stream_agent() -> Agent:
    """Streaming variant for the SSE endpoint (ARCHITECTURE.md §7 word-by-word
    UX): no output_schema — with one set, agno buffers the whole response to
    parse it and emits a single event instead of token deltas.
    """
    settings = get_settings()
    return Agent(
        name="script_stream",
        model=OpenRouter(id=settings.openrouter_model, api_key=settings.openrouter_api_key),
        description=(
            "Writes a short-form video script in the creator's Voice DNA, "
            "as plain flowing text with clear Hook, Body and CTA sections."
        ),
        markdown=False,
    )
