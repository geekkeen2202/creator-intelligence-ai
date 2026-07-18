from functools import partial

import httpx

from app.adapters import youtube_adapter, youtube_trends_adapter
from app.adapters.facebook_adapter import FacebookAdapter
from app.adapters.x_adapter import XAdapter
from app.adapters.youtube_adapter import YouTubeAdapter
from app.adapters.youtube_trends_adapter import YouTubeTrendsAdapter

_CHART_PAYLOAD = {
    "items": [
        {
            "id": "abc123",
            "snippet": {
                "title": "AI breakthrough explained",
                "description": "Long description " * 100,
                "channelId": "ch9",
                "channelTitle": "Tech Guru",
                "publishedAt": "2026-07-15T10:00:00Z",
            },
            "statistics": {"viewCount": "123456"},
        }
    ]
}


def _mock_client(module, monkeypatch, payload, captured_params):
    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )


async def test_youtube_trends_adapter_normalizes_and_maps_category(monkeypatch):
    params: dict = {}
    _mock_client(youtube_trends_adapter, monkeypatch, _CHART_PAYLOAD, params)
    adapter = YouTubeTrendsAdapter()
    adapter._api_key = "test-key"

    items = await adapter.fetch_trending_topics("technology", "en")

    assert params["videoCategoryId"] == "28"  # numeric ID from NICHE_MAP, not the niche string
    assert params["regionCode"] == "IN"
    assert len(items) == 1
    item = items[0]
    assert item.title == "AI breakthrough explained"
    assert item.url == "https://www.youtube.com/watch?v=abc123"
    assert item.score == 123456
    assert len(item.summary) <= 500
    assert item.metadata["channel_title"] == "Tech Guru"


async def test_youtube_adapter_search_top_videos_params(monkeypatch):
    params: dict = {}
    _mock_client(youtube_adapter, monkeypatch, {"items": [{"id": {"videoId": "v1"}}]}, params)
    adapter = YouTubeAdapter()
    adapter._api_key = "test-key"

    items = await adapter.search_top_videos(
        ["AI", "smartphone"],
        category_id="28",
        language="hi",
        published_after="2026-07-01T00:00:00Z",
    )

    assert params["q"] == "AI | smartphone"
    assert params["order"] == "viewCount"
    assert params["videoCategoryId"] == "28"
    assert params["relevanceLanguage"] == "hi"
    assert params["publishedAfter"] == "2026-07-01T00:00:00Z"
    assert items == [{"id": {"videoId": "v1"}}]


async def test_youtube_adapter_get_videos_stats_batches_ids(monkeypatch):
    params: dict = {}
    _mock_client(youtube_adapter, monkeypatch, {"items": []}, params)
    adapter = YouTubeAdapter()
    adapter._api_key = "test-key"

    assert await adapter.get_videos_stats([]) == []  # no API call for empty input
    await adapter.get_videos_stats([f"v{i}" for i in range(60)])
    assert len(params["id"].split(",")) == 50  # API caps at 50 ids


async def test_stub_adapters_disabled_without_keys():
    assert XAdapter().is_enabled() is False
    assert FacebookAdapter().is_enabled() is False
    assert await XAdapter().fetch_trending_topics("technology", "en") == []
    assert await FacebookAdapter().fetch_trending_topics("technology", "en") == []


def test_enabled_flags_follow_api_keys():
    adapter = YouTubeTrendsAdapter()
    adapter._api_key = ""
    assert adapter.is_enabled() is False
    adapter._api_key = "k"
    assert adapter.is_enabled() is True
