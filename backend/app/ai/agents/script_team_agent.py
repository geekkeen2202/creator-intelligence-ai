from agno.agent import Agent
from agno.models.openrouter import OpenRouter
from agno.team import Team

from app.ai.agents.script_agent import GeneratedScript
from app.config import get_settings

VERSION = "v1"
PROMPT_VERSION = "v1"


def build_premium_script_team() -> Team:
    """Multi-agent team for script generation — paid tier.

    Splits hook/body/cta writing across specialist agents, reviewed by an editor agent.
    """
    settings = get_settings()
    model = OpenRouter(id=settings.openrouter_model, api_key=settings.openrouter_api_key)

    hook_writer = Agent(name="hook_writer", model=model, description="Writes high-retention hooks.")
    body_writer = Agent(name="body_writer", model=model, description="Writes the script body.")
    cta_writer = Agent(name="cta_writer", model=model, description="Writes calls to action.")
    editor = Agent(
        name="editor",
        model=model,
        description="Merges and polishes the final script in the creator's Voice DNA.",
        output_schema=GeneratedScript,
    )

    return Team(
        name="script_team",
        mode="coordinate",
        model=model,
        members=[hook_writer, body_writer, cta_writer, editor],
        output_schema=GeneratedScript,
    )
