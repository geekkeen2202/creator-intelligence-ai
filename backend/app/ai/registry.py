from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.ai.agents import (
    script_agent,
    script_team_agent,
    thumbnail_brief_agent,
    trending_agent,
    voice_dna_agent,
)


@dataclass(frozen=True)
class AgentEntry:
    build: Callable[[], Any]
    version: str
    prompt_version: str


# Adding an agent = one entry here + one file in app/ai/agents/. Every entry
# carries version identifiers (ARCHITECTURE.md §7) so callers can stamp
# provenance on rows without hardcoding version strings per-module.
AGENT_REGISTRY: dict[str, AgentEntry] = {
    "voice_dna": AgentEntry(
        voice_dna_agent.build_voice_dna_agent,
        voice_dna_agent.VERSION,
        voice_dna_agent.PROMPT_VERSION,
    ),
    "trending": AgentEntry(
        trending_agent.build_trending_agent,
        trending_agent.VERSION,
        trending_agent.PROMPT_VERSION,
    ),
    "script": AgentEntry(
        script_agent.build_script_agent, script_agent.VERSION, script_agent.PROMPT_VERSION
    ),
    "script_stream": AgentEntry(
        script_agent.build_script_stream_agent, script_agent.VERSION, script_agent.PROMPT_VERSION
    ),
    "script_team": AgentEntry(
        script_team_agent.build_premium_script_team,
        script_team_agent.VERSION,
        script_team_agent.PROMPT_VERSION,
    ),
    "thumbnail_brief": AgentEntry(
        thumbnail_brief_agent.build_thumbnail_brief_agent,
        thumbnail_brief_agent.VERSION,
        thumbnail_brief_agent.PROMPT_VERSION,
    ),
}


def get_agent(name: str) -> Any:
    return AGENT_REGISTRY[name].build()


def get_agent_entry(name: str) -> AgentEntry:
    return AGENT_REGISTRY[name]
