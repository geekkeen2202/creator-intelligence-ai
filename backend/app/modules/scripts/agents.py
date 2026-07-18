"""Agno agents this module owns — registered centrally in app/ai/registry.py."""

from app.ai.agents.script_agent import build_script_agent, build_script_stream_agent
from app.ai.registry import AgentEntry, get_agent, get_agent_entry


def get_script_agent(platform: str = "youtube_long"):
    # Model routing by task type (TechnicalDesign.md §5.2) lives in
    # build_script_agent — bypasses the zero-arg registry entry so the
    # platform can select cheap-vs-full model.
    return build_script_agent(platform)


def get_script_team():
    return get_agent("script_team")


def get_script_stream_agent(platform: str = "youtube_long"):
    return build_script_stream_agent(platform)


def get_script_agent_entry(premium: bool) -> AgentEntry:
    return get_agent_entry("script_team" if premium else "script")
