"""Agno agents this module owns — registered centrally in app/ai/registry.py."""

from app.ai.registry import AgentEntry, get_agent, get_agent_entry


def get_script_agent():
    return get_agent("script")


def get_script_team():
    return get_agent("script_team")


def get_script_stream_agent():
    return get_agent("script_stream")


def get_script_agent_entry(premium: bool) -> AgentEntry:
    return get_agent_entry("script_team" if premium else "script")
