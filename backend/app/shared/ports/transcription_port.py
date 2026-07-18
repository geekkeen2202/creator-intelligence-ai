from typing import Protocol


class TranscriptionPort(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> tuple[str, float, str]:
        """Returns (text, quality_score, language_detected). Fallback path
        when caption quality gate fails (ARCHITECTURE.md §2/§11)."""
        ...
