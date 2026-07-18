from agno.agent import Agent
from agno.models.openrouter import OpenRouter
from pydantic import BaseModel

from app.config import get_settings

VERSION = "v1"
PROMPT_VERSION = "v1"

# TechnicalDesign.md §7 — platforms whose scripts are short-form; routed to
# the cheaper/faster model (§5.2 "Model routing by task type").
SHORT_FORM_PLATFORMS = frozenset({"youtube_shorts", "instagram_reels", "tiktok"})


class GeneratedScript(BaseModel):
    hook: str
    body: str
    cta: str
    b_roll_suggestions: list[str] = []
    power_word_spans: list[str] = []
    estimated_duration_seconds: float | None = None


def _model_for(platform: str) -> OpenRouter:
    settings = get_settings()
    model_id = (
        settings.openrouter_fast_model or settings.openrouter_model
        if platform in SHORT_FORM_PLATFORMS
        else settings.openrouter_model
    )
    return OpenRouter(id=model_id, api_key=settings.openrouter_api_key)


def build_script_agent(platform: str = "youtube_long") -> Agent:
    """Single-agent script generation — free tier."""
    return Agent(
        name="script",
        model=_model_for(platform),
        description="Writes a short-form video script in the creator's Voice DNA.",
        output_schema=GeneratedScript,
        markdown=False,
    )


def build_script_stream_agent(platform: str = "youtube_long") -> Agent:
    """Streaming variant for the SSE endpoint (ARCHITECTURE.md §7 word-by-word
    UX): no output_schema — with one set, agno buffers the whole response to
    parse it and emits a single event instead of token deltas.
    """
    return Agent(
        name="script_stream",
        model=_model_for(platform),
        description=(
            "Writes a short-form video script in the creator's Voice DNA, "
            "as plain flowing text with clear Hook, Body and CTA sections."
        ),
        markdown=False,
    )
