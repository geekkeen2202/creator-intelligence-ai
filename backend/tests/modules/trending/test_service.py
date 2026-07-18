from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.trending import service as service_module
from app.modules.trending.models import TrendingTopic, TrendingVideo
from app.modules.trending.schemas import VideoContext
from app.modules.trending.service import TrendingService
from app.shared.ports.trend_source_port import RawTrendItem


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys):
        self.deleted.extend(keys)
        for key in keys:
            self.store.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds):
        pass  # TTL not modeled in the fake; tests don't rely on expiry


class FakeRepository:
    def __init__(self):
        self.topics: list[TrendingTopic] = []
        self.videos: list[TrendingVideo] = []
        self.soft_deleted: list[tuple[str, str]] = []
        self.ingested_topic_batches: set[tuple[str, str]] = set()
        self.ingested_video_batches: set[tuple[str, str]] = set()
        self.assignments: dict = {}

    async def list_active(self, niche, language, source=None):
        return [t for t in self.topics if source is None or t.source == source]

    async def list_active_videos(self, niche, language):
        return self.videos

    async def has_ingested_batch(self, niche, language):
        return (niche, language) in self.ingested_topic_batches

    async def has_ingested_video_batch(self, niche, language):
        return (niche, language) in self.ingested_video_batches

    async def get_niche_for_channel(self, channel_id):
        return self.assignments.get(channel_id)

    async def soft_delete_batch(self, niche, language):
        self.soft_deleted.append((niche, language))

    async def bulk_create(self, topics):
        self.topics.extend(topics)
        for t in topics:
            self.ingested_topic_batches.add((t.niche, t.language))

    async def bulk_create_videos(self, videos):
        self.videos.extend(videos)
        for v in videos:
            self.ingested_video_batches.add((v.niche, v.language))


class FakeSource:
    def __init__(self, source, items=None, error=None, enabled=True):
        self.source = source
        self._items = items or []
        self._error = error
        self._enabled = enabled
        self.called = False

    def is_enabled(self):
        return self._enabled

    async def fetch_trending_topics(self, niche, language):
        self.called = True
        if self._error:
            raise self._error
        return self._items


def _search_item(video_id):
    return {"id": {"videoId": video_id}}


def _video_detail(video_id, views, title="Video", description="desc"):
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "channelId": "ch1",
            "channelTitle": "Top Creator",
            "publishedAt": "2026-07-10T00:00:00Z",
        },
        "statistics": {"viewCount": str(views), "likeCount": "10"},
    }


class FakeVideoSource:
    def __init__(self, search_items, details):
        self._search_items = search_items
        self._details = details

    async def search_top_videos(self, keywords, **kwargs):
        return self._search_items

    async def get_videos_stats(self, video_ids):
        return [d for d in self._details if d["id"] in video_ids]


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def generate_structured(self, prompt, response_model, *, system=None):
        self.calls += 1
        return VideoContext(
            summary="A distilled summary", key_points=["p1"], hook_style="question", angle="how-to"
        )


@pytest.fixture
def repo():
    return FakeRepository()


@pytest.fixture
def redis():
    return FakeRedis()


async def test_ingest_writes_topics_tagged_by_source(repo, redis):
    sources = [
        FakeSource("youtube", items=[RawTrendItem(title="YT topic", score=100)]),
        FakeSource("reddit", items=[RawTrendItem(title="Reddit topic", score=50)]),
        FakeSource("x", enabled=False),
    ]
    service = TrendingService(repo, redis, trend_sources=sources)

    await service.ingest("technology", "en")

    assert {t.source for t in repo.topics} == {"youtube", "reddit"}
    assert repo.soft_deleted == [("technology", "en")]
    assert not sources[2].called  # disabled source never hit
    assert all(t.expires_at > datetime.now(UTC) for t in repo.topics)


async def test_ingest_isolates_single_source_failure(repo, redis):
    sources = [
        FakeSource("youtube", error=RuntimeError("quota exceeded")),
        FakeSource("reddit", items=[RawTrendItem(title="Still works", score=1)]),
    ]
    service = TrendingService(repo, redis, trend_sources=sources)

    await service.ingest("technology", "en")

    assert [t.source for t in repo.topics] == ["reddit"]


async def test_circuit_breaker_skips_source_after_repeated_failures(repo, redis):
    flaky = FakeSource("google_trends", error=RuntimeError("429 Too Many Requests"))
    service = TrendingService(repo, redis, trend_sources=[flaky])

    for _ in range(3):
        flaky.called = False
        await service.ingest("technology", "en")
        assert flaky.called  # each of the first 3 failures actually calls the source

    flaky.called = False
    await service.ingest("technology", "en")

    assert not flaky.called  # circuit now open — source skipped without a network call


async def test_circuit_breaker_resets_on_success(repo, redis):
    flaky = FakeSource("google_trends", error=RuntimeError("429 Too Many Requests"))
    service = TrendingService(repo, redis, trend_sources=[flaky])

    await service.ingest("technology", "en")
    await service.ingest("technology", "en")
    flaky._error = None
    flaky._items = [RawTrendItem(title="Recovered", score=1)]
    await service.ingest("technology", "en")  # success resets the failure count

    flaky._error = RuntimeError("429 again")
    flaky.called = False
    await service.ingest("technology", "en")
    assert flaky.called  # still under threshold post-reset, source is tried again


async def test_rate_limiter_skips_source_once_budget_exhausted(repo, redis):
    from app.modules.trending import service as svc

    source = FakeSource("youtube", items=[RawTrendItem(title="T", score=1)])
    service = TrendingService(repo, redis, trend_sources=[source])

    budget = svc._EXTERNAL_RATE_LIMIT_PER_MINUTE
    for _ in range(budget):
        source.called = False
        await service.ingest("technology", "en")
        assert source.called  # within budget: source is called every time

    source.called = False
    await service.ingest("technology", "en")

    assert not source.called  # over budget this window — skipped without a network call


async def test_rate_limiter_is_per_source_and_resets_next_window(repo, redis):
    from app.modules.trending import service as svc

    exhausted = FakeSource("youtube", items=[RawTrendItem(title="T", score=1)])
    only_exhausted = TrendingService(repo, redis, trend_sources=[exhausted])
    for _ in range(svc._EXTERNAL_RATE_LIMIT_PER_MINUTE):
        await only_exhausted.ingest("technology", "en")

    fresh = FakeSource("reddit", items=[RawTrendItem(title="R", score=1)])
    service = TrendingService(repo, redis, trend_sources=[exhausted, fresh])
    exhausted.called = False
    fresh.called = False
    await service.ingest("technology", "en")

    assert not exhausted.called  # exhausted source skipped
    assert fresh.called  # a different source's budget is unaffected

    redis.store.pop(f"ratelimit:external:{exhausted.source}", None)  # simulate window rollover
    exhausted.called = False
    await service.ingest("technology", "en")
    assert exhausted.called  # new window: source is tried again


async def test_ingest_ranks_videos_and_summarizes_top(repo, redis):
    details = [_video_detail(f"v{i}", views=i * 1000) for i in range(1, 26)]
    video_source = FakeVideoSource([_search_item(f"v{i}") for i in range(1, 26)], details)
    llm = FakeLLM()
    service = TrendingService(repo, redis, video_source=video_source, llm=llm)

    await service.ingest("technology", "en")

    assert len(repo.videos) == 20  # top 20 kept out of 25
    assert repo.videos[0].video_id == "v25"  # highest views first
    assert llm.calls == 10  # only top 10 summarized
    assert repo.videos[0].summary == "A distilled summary"
    assert repo.videos[0].context["hook_style"] == "question"
    assert repo.videos[0].context["description"] == "desc"  # metadata kept alongside LLM output
    assert repo.videos[-1].summary == ""  # beyond top 10: metadata only


async def test_ingest_without_llm_stores_metadata_only(repo, redis):
    video_source = FakeVideoSource([_search_item("v1")], [_video_detail("v1", views=500)])
    service = TrendingService(repo, redis, video_source=video_source, llm=None)

    await service.ingest("technology", "en")

    assert len(repo.videos) == 1
    assert repo.videos[0].summary == ""
    assert repo.videos[0].stats["viewCount"] == "500"


async def test_ingest_invalidates_caches_and_emits_after_write(repo, redis, monkeypatch):
    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )
    service = TrendingService(
        repo, redis, trend_sources=[FakeSource("youtube", items=[RawTrendItem(title="T")])]
    )

    await service.ingest("gaming", "hi")

    assert "trending:gaming:hi" in redis.deleted
    assert "trending:videos:gaming:hi" in redis.deleted
    assert events == [
        (
            "trending.refreshed",
            {"niche": "gaming", "language": "hi", "topic_count": 1, "video_count": 0},
        )
    ]


def _topic(source="youtube", score=1.0):
    return TrendingTopic(
        id=uuid4(),
        niche="technology",
        language="en",
        source=source,
        title="T",
        summary="",
        score=score,
        payload={},
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )


async def test_get_trending_caches_db_result(repo, redis):
    repo.topics = [_topic()]
    service = TrendingService(repo, redis)

    first = await service.get_trending("technology", "en")
    repo.topics = []  # second read must come from cache
    second = await service.get_trending("technology", "en")

    assert [t.title for t in first] == ["T"]
    assert [t.title for t in second] == ["T"]


async def test_get_trending_source_filter_bypasses_cache(repo, redis):
    repo.topics = [_topic("youtube"), _topic("reddit")]
    service = TrendingService(repo, redis)

    result = await service.get_trending("technology", "en", source="reddit")

    assert [t.source for t in result] == ["reddit"]


def _topic_titled(title, score=1.0, niche="technology", language="en"):
    return TrendingTopic(
        id=uuid4(),
        niche=niche,
        language=language,
        source="youtube",
        title=title,
        summary="",
        score=score,
        payload={},
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )


async def test_get_channel_context_reranks_by_channel_keywords(repo, redis):
    repo.ingested_topic_batches.add(("technology", "en"))
    repo.topics = [
        _topic_titled("Smartphone camera tips", score=5),
        _topic_titled("Cloud computing basics", score=10),
    ]
    channel_id = uuid4()
    repo.assignments[channel_id] = SimpleNamespace(
        niche="technology", keywords=["smartphone", "camera"]
    )
    service = TrendingService(repo, redis)

    context = await service.get_channel_context(channel_id)

    assert context.topics[0].title == "Smartphone camera tips"  # keyword overlap outranks raw score


async def test_get_channel_context_falls_back_to_general_when_unassigned(repo, redis):
    repo.ingested_topic_batches.add(("general", "en"))
    repo.topics = [_topic_titled("General topic", niche="general")]
    service = TrendingService(repo, redis)

    context = await service.get_channel_context(uuid4())

    assert context.niche == "general"
    assert [t.title for t in context.topics] == ["General topic"]


async def test_get_channel_context_without_keywords_keeps_niche_order(repo, redis):
    repo.ingested_topic_batches.add(("technology", "en"))
    repo.topics = [_topic_titled("A", score=10), _topic_titled("B", score=5)]
    channel_id = uuid4()
    repo.assignments[channel_id] = SimpleNamespace(niche="technology", keywords=[])
    service = TrendingService(repo, redis)

    context = await service.get_channel_context(channel_id)

    assert [t.title for t in context.topics] == ["A", "B"]  # unchanged order, plain score


async def test_cold_start_triggers_ingest_for_never_seen_niche(repo, redis, monkeypatch):
    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )
    service = TrendingService(repo, redis)

    result = await service.get_trending("technology", "en")

    assert result == []
    assert events == [("trending.cold_niche_requested", {"niche": "technology", "language": "en"})]


async def test_cold_start_not_triggered_for_stale_niche(repo, redis, monkeypatch):
    repo.ingested_topic_batches.add(
        ("technology", "en")
    )  # previously ingested, just currently stale
    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )
    service = TrendingService(repo, redis)

    await service.get_trending("technology", "en")

    assert events == []


async def test_cold_start_triggers_for_videos_even_when_topics_already_ingested(
    repo, redis, monkeypatch
):
    # Regression: a niche can have topics but zero videos ever written (e.g.
    # YOUTUBE_API_KEY was unset on the run that ingested topics). Reading
    # videos later must still cold-start, not assume "stale, wait for beat".
    repo.ingested_topic_batches.add(("technology", "en"))
    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )
    service = TrendingService(repo, redis)

    result = await service.get_trending_videos("technology", "en")

    assert result == []
    assert events == [("trending.cold_niche_requested", {"niche": "technology", "language": "en"})]


async def test_cold_start_not_triggered_for_videos_once_video_batch_exists(
    repo, redis, monkeypatch
):
    repo.ingested_topic_batches.add(("technology", "en"))
    repo.ingested_video_batches.add(("technology", "en"))  # videos ran too, just currently stale
    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )
    service = TrendingService(repo, redis)

    await service.get_trending_videos("technology", "en")

    assert events == []


async def test_cold_start_deduped_by_lock(repo, redis, monkeypatch):
    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )
    service = TrendingService(repo, redis)

    await service.get_trending("technology", "en")
    await service.get_trending("technology", "en")  # second call within lock TTL: no duplicate

    assert len(events) == 1
