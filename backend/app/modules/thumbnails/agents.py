"""Agno agent this module owns — registered centrally in app/ai/registry.py."""

from app.ai.registry import AgentEntry, get_agent, get_agent_entry


def get_thumbnail_brief_agent():
    return get_agent("thumbnail_brief")


def get_thumbnail_brief_agent_entry() -> AgentEntry:
    return get_agent_entry("thumbnail_brief")
