import io

from openai import AsyncOpenAI

from app.config import get_settings

# Whisper doesn't return a confidence score directly — a fixed, conservative
# value is used so it's always ranked below real caption-derived quality
# scores (captions are preferred whenever available; see YouTubeAdapter).
_WHISPER_QUALITY_SCORE = 0.6


class WhisperAdapter:
    """Implements TranscriptionPort (app/shared/ports/transcription_port.py)
    via OpenAI's Whisper API — fallback when YouTube captions are unavailable
    or fail the quality gate.
    """

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=get_settings().openai_api_key)

    async def transcribe(self, audio_bytes: bytes) -> tuple[str, float]:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.mp3"
        response = await self._client.audio.transcriptions.create(
            model="whisper-1", file=audio_file
        )
        return response.text, _WHISPER_QUALITY_SCORE
