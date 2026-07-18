from typing import Any, Protocol


class SocialPlatformPort(Protocol):
    async def get_channel_profile(self, channel_id: str) -> dict[str, Any]: ...

    async def get_recent_videos(self, channel_id: str, limit: int = 25) -> list[dict[str, Any]]: ...

    async def get_video_analytics(self, video_id: str) -> dict[str, Any]: ...

    async def search_top_videos(
        self,
        keywords: list[str],
        *,
        category_id: str | None = None,
        language: str = "en",
        published_after: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]: ...

    async def get_videos_stats(self, video_ids: list[str]) -> list[dict[str, Any]]: ...

    async def get_captions(self, video_id: str) -> tuple[str, float, str] | None: ...

    async def download_audio(self, video_id: str) -> bytes | None: ...
