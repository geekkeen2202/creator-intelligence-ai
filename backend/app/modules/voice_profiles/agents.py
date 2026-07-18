"""Agno agent this module owns — registered centrally in app/ai/registry.py."""

from app.ai.registry import AgentEntry, get_agent, get_agent_entry


def get_voice_dna_agent():
    return get_agent("voice_dna")


def get_voice_dna_agent_entry() -> AgentEntry:
    return get_agent_entry("voice_dna")
