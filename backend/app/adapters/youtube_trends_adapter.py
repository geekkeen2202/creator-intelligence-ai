import httpx

from app.config import get_settings
from app.modules.trending.niches import get_niche_config
from app.shared.ports.trend_source_port import RawTrendItem

_BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeTrendsAdapter:
    """Implements TrendSourcePort using YouTube's mostPopular videos chart."""

    source = "youtube"

    def __init__(self) -> None:
        self._api_key = get_settings().youtube_api_key

    def is_enabled(self) -> bool:
        return bool(self._api_key)

    async def fetch_trending_topics(self, niche: str, language: str) -> list[RawTrendItem]:
        config = get_niche_config(niche)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE_URL}/videos",
                params={
                    "part": "snippet,statistics",
                    "chart": "mostPopular",
                    "regionCode": "IN",
                    "videoCategoryId": config.youtube_category_id,
                    "maxResults": 25,
                    "hl": language,
                    "key": self._api_key,
                },
            )
            response.raise_for_status()
            items = response.json().get("items", [])

        return [
            RawTrendItem(
                title=item["snippet"]["title"],
                summary=item["snippet"].get("description", "")[:500],
                url=f"https://www.youtube.com/watch?v={item['id']}",
                score=float(item.get("statistics", {}).get("viewCount", 0)),
                metadata={
                    "video_id": item["id"],
                    "channel_id": item["snippet"].get("channelId", ""),
                    "channel_title": item["snippet"].get("channelTitle", ""),
                    "published_at": item["snippet"].get("publishedAt", ""),
                    "statistics": item.get("statistics", {}),
                },
            )
            for item in items
        ]
