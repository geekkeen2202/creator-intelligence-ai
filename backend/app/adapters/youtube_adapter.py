from typing import Any

import httpx
import structlog
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

from app.config import get_settings

log = structlog.get_logger(__name__)

_BASE_URL = "https://www.googleapis.com/youtube/v3"

# Caption-derived text is high quality (human/auto captions, no ASR noise) —
# fixed above the Whisper fallback's score (see WhisperAdapter).
_CAPTION_QUALITY_SCORE = 0.9


class YouTubeAdapter:
    """Implements SocialPlatformPort via the YouTube Data API v3."""

    def __init__(self) -> None:
        self._api_key = get_settings().youtube_api_key

    async def get_channel_profile(self, channel_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/channels",
                params={
                    "part": "snippet,statistics,brandingSettings",
                    "id": channel_id,
                    "key": self._api_key,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_recent_videos(self, channel_id: str, limit: int = 25) -> list[dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/search",
                params={
                    "part": "snippet",
                    "channelId": channel_id,
                    "order": "date",
                    "maxResults": limit,
                    "type": "video",
                    "key": self._api_key,
                },
            )
            response.raise_for_status()
            return response.json().get("items", [])

    async def get_video_analytics(self, video_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/videos",
                params={"part": "statistics,contentDetails", "id": video_id, "key": self._api_key},
            )
            response.raise_for_status()
            return response.json()

    async def search_top_videos(
        self,
        keywords: list[str],
        *,
        category_id: str | None = None,
        language: str = "en",
        published_after: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "part": "snippet",
            "q": " | ".join(keywords),
            "type": "video",
            "order": "viewCount",
            "regionCode": "IN",
            "relevanceLanguage": language,
            "maxResults": limit,
            "key": self._api_key,
        }
        if category_id:
            params["videoCategoryId"] = category_id
        if published_after:
            params["publishedAfter"] = published_after
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{_BASE_URL}/search", params=params)
            response.raise_for_status()
            return response.json().get("items", [])

    async def get_captions(self, video_id: str) -> tuple[str, float, str] | None:
        """Public captions via the timedtext endpoint (no OAuth required —
        unlike the Data API's captions.download, which needs the channel
        owner's consent token). Returns None when no captions are available,
        which signals the caller to fall back to Whisper.
        """
        try:
            transcript = YouTubeTranscriptApi().fetch(video_id)
        except CouldNotRetrieveTranscript:
            return None
        text = " ".join(snippet.text for snippet in transcript).strip()
        if not text:
            return None
        return text, _CAPTION_QUALITY_SCORE, transcript.language_code

    async def download_audio(self, video_id: str) -> bytes | None:
        """Audio for the Whisper fallback path — only ever called for a
        video on a channel the requesting user owns/connected (creator
        analyzing their own content), not third-party scraping. yt-dlp runs
        in a thread since it's blocking I/O.
        """
        import asyncio

        try:
            return await asyncio.to_thread(self._download_audio_sync, video_id)
        except Exception:
            log.warning("youtube_audio_download_failed", video_id=video_id)
            return None

    def _download_audio_sync(self, video_id: str) -> bytes | None:
        import tempfile
        from pathlib import Path

        import yt_dlp

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / f"{video_id}.mp3"
            opts = {
                "format": "bestaudio/best",
                "outtmpl": str(out_path.with_suffix("")) + ".%(ext)s",
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"},
                ],
                "quiet": True,
                "noprogress": True,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            if not out_path.exists():
                return None
            return out_path.read_bytes()

    async def get_videos_stats(self, video_ids: list[str]) -> list[dict[str, Any]]:
        if not video_ids:
            return []
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/videos",
                params={
                    "part": "snippet,statistics",
                    "id": ",".join(video_ids[:50]),  # API caps at 50 ids per call
                    "key": self._api_key,
                },
            )
            response.raise_for_status()
            return response.json().get("items", [])
