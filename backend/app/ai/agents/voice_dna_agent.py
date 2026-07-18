from agno.agent import Agent
from agno.models.openrouter import OpenRouter
from pydantic import BaseModel

from app.config import get_settings

VERSION = "v1"
PROMPT_VERSION = "v1"


class VoiceDNA(BaseModel):
    tone: str
    pacing: str
    vocabulary_level: str
    signature_phrases: list[str]
    hook_style: str
    cta_style: str


def build_voice_dna_agent() -> Agent:
    settings = get_settings()
    return Agent(
        name="voice_dna",
        model=OpenRouter(id=settings.openrouter_model, api_key=settings.openrouter_api_key),
        description="Analyzes a creator's past videos/scripts and extracts their Voice DNA.",
        output_schema=VoiceDNA,
    )
