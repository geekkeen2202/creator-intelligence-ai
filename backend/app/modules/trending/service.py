import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import prompts
from app.modules.trending.classifier import tokenize
from app.modules.trending.models import TrendingTopic, TrendingVideo
from app.modules.trending.niches import get_niche_config
from app.modules.trending.repository import TrendingRepository
from app.modules.trending.schemas import (
    NicheContext,
    TrendingTopicRead,
    TrendingVideoRead,
    VideoContext,
)
from app.shared.events import TRENDING_COLD_NICHE_REQUESTED, TRENDING_REFRESHED, emit
from app.shared.ports.llm_port import LLMPort
from app.shared.ports.social_platform_port import SocialPlatformPort
from app.shared.ports.trend_source_port import TrendSourcePort
from app.shared.ratelimit import RedisRateLimiter

log = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 24 * 60 * 60
_BATCH_TTL = timedelta(hours=24)
_DISCOVERY_WINDOW_DAYS = 14
_TOP_VIDEOS_KEPT = 20
_VIDEOS_SUMMARIZED = 10

# Circuit breaker for unofficial/unstable sources (e.g. Google Trends scraping,
# no API key, no SLA, IP-based rate limits). After repeated failures within a
# window, skip the source entirely for a cooldown instead of hammering it —
# and instead of even attempting the call, which wastes worker time on a batch
# that is already burst-heavy.
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_FAILURE_WINDOW_SECONDS = 60 * 60
_CIRCUIT_COOLDOWN_SECONDS = 60 * 60

# Aggregate rate budget per external source, shared across every Celery worker
# and every niche. This is what stays safe as the canonical niche set grows
# (or workers scale out) — call volume is capped independent of how many
# (niche, language) pairs exist, not just of user/channel count.
_EXTERNAL_RATE_LIMIT_PER_MINUTE = 20
_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS = 60

# Cold-niche on-demand ingestion: dedupe concurrent triggers for a niche with
# no data yet, without blocking the request (ARCHITECTURE.md §10).
_COLD_NICHE_LOCK_TTL_SECONDS = 10 * 60

_SUMMARY_PROMPT_FEATURE = "trending_summarize"
_DEFAULT_SUMMARY_SYSTEM_PROMPT = (
    "You analyze YouTube video metadata for content creators researching their niche. "
    "From the title, description, tags and stats, distill what the video covers, "
    "why it is performing, the hook style of its title, and the content angle."
)


class TrendingService:
    def __init__(
        self,
        repository: TrendingRepository,
        redis: Redis,
        db: AsyncSession | None = None,
        trend_sources: list[TrendSourcePort] | None = None,
        video_source: SocialPlatformPort | None = None,
        llm: LLMPort | None = None,
    ):
        self._repository = repository
        self._redis = redis
        self._db = db
        self._trend_sources = trend_sources or []
        self._video_source = video_source
        self._llm = llm

    # ------------------------------------------------------------------ reads

    async def get_trending(
        self, niche: str, language: str, source: str | None = None
    ) -> list[TrendingTopicRead]:
        # Never call external trend APIs on request (see ARCHITECTURE.md §10) —
        # this reads cache, falling back to the DB rows written by ingest.
        if source is not None:
            topics = await self._repository.list_active(niche, language, source)
            if not topics:
                await self._maybe_trigger_cold_start(niche, language)
            return [TrendingTopicRead.model_validate(t) for t in topics]

        cache_key = f"trending:{niche}:{language}"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return [TrendingTopicRead.model_validate(item) for item in json.loads(cached)]

        topics = await self._repository.list_active(niche, language)
        if not topics:
            await self._maybe_trigger_cold_start(niche, language)
        payload = [TrendingTopicRead.model_validate(t).model_dump(mode="json") for t in topics]
        await self._redis.set(cache_key, json.dumps(payload), ex=_CACHE_TTL_SECONDS)
        return [TrendingTopicRead.model_validate(item) for item in payload]

    async def get_trending_videos(self, niche: str, language: str) -> list[TrendingVideoRead]:
        cache_key = f"trending:videos:{niche}:{language}"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return [TrendingVideoRead.model_validate(item) for item in json.loads(cached)]

        videos = await self._repository.list_active_videos(niche, language)
        if not videos:
            await self._maybe_trigger_cold_start(niche, language, video=True)
        payload = [TrendingVideoRead.model_validate(v).model_dump(mode="json") for v in videos]
        await self._redis.set(cache_key, json.dumps(payload), ex=_CACHE_TTL_SECONDS)
        return [TrendingVideoRead.model_validate(item) for item in payload]

    async def get_niche_context(self, niche: str, language: str) -> NicheContext:
        return NicheContext(
            niche=niche,
            language=language,
            topics=await self.get_trending(niche, language),
            videos=await self.get_trending_videos(niche, language),
        )

    async def get_channel_context(self, channel_id: UUID, language: str = "en") -> NicheContext:
        """Personalized read: same shared niche batch, re-ranked per channel —
        zero extra external calls regardless of how many channels call this.
        """
        assignment = await self._repository.get_niche_for_channel(channel_id)
        niche = assignment.niche if assignment else "general"
        keywords = assignment.keywords if assignment else []

        context = await self.get_niche_context(niche, language)
        if not keywords:
            return context

        keyword_tokens = set()
        for keyword in keywords:
            keyword_tokens |= tokenize(keyword)

        context.topics = sorted(
            context.topics,
            key=lambda t: (len(tokenize(f"{t.title} {t.summary}") & keyword_tokens), t.score),
            reverse=True,
        )
        context.videos = sorted(
            context.videos,
            key=lambda v: (len(tokenize(f"{v.title} {v.summary}") & keyword_tokens), v.score),
            reverse=True,
        )
        return context

    # --------------------------------------------------------- cold-niche start

    async def _maybe_trigger_cold_start(
        self, niche: str, language: str, video: bool = False
    ) -> None:
        # Checked independently: a niche can have topics but zero videos ever
        # written (e.g. YOUTUBE_API_KEY was unset when it first ingested), so
        # "topics exist" must not suppress a cold-start needed for videos.
        already_ingested = (
            await self._repository.has_ingested_video_batch(niche, language)
            if video
            else await self._repository.has_ingested_batch(niche, language)
        )
        if already_ingested:
            return  # stale, not cold — the next scheduled beat run will refresh it
        lock_key = f"trending:coldstart:lock:{niche}:{language}"
        acquired = await self._redis.set(
            lock_key, "1", ex=_COLD_NICHE_LOCK_TTL_SECONDS, nx=True
        )
        if not acquired:
            return  # another concurrent request already triggered this
        log.info("trending_cold_niche_triggered", niche=niche, language=language)
        emit(TRENDING_COLD_NICHE_REQUESTED, {"niche": niche, "language": language})

    # ----------------------------------------------------------------- ingest

    async def ingest(self, niche: str, language: str) -> None:
        """Full refresh for one (niche, language): topic signals + competitor videos."""
        expires_at = datetime.now(UTC) + _BATCH_TTL

        topics = await self._fetch_topics(niche, language, expires_at)
        videos = await self._discover_videos(niche, language, expires_at)
        await self._summarize_videos(videos)

        await self._repository.soft_delete_batch(niche, language)
        if topics:
            await self._repository.bulk_create(topics)
        if videos:
            await self._repository.bulk_create_videos(videos)

        await self._redis.delete(
            f"trending:{niche}:{language}", f"trending:videos:{niche}:{language}"
        )
        emit(
            TRENDING_REFRESHED,
            {
                "niche": niche,
                "language": language,
                "topic_count": len(topics),
                "video_count": len(videos),
            },
        )

    async def _fetch_topics(
        self, niche: str, language: str, expires_at: datetime
    ) -> list[TrendingTopic]:
        candidates = [s for s in self._trend_sources if s.is_enabled()]
        enabled = []
        for source in candidates:
            if await self._is_circuit_open(source.source):
                log.info("trend_source_circuit_open_skip", source=source.source, niche=niche)
                continue
            if not await self._check_external_rate_limit(source.source):
                log.info("trend_source_rate_limited_skip", source=source.source, niche=niche)
                continue
            enabled.append(source)

        results = await asyncio.gather(
            *(s.fetch_trending_topics(niche, language) for s in enabled),
            return_exceptions=True,
        )
        topics: list[TrendingTopic] = []
        for adapter, result in zip(enabled, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "trend_source_failed", source=adapter.source, niche=niche, error=str(result)
                )
                await self._record_circuit_failure(adapter.source)
                continue
            await self._record_circuit_success(adapter.source)
            topics.extend(
                TrendingTopic(
                    niche=niche,
                    language=language,
                    source=adapter.source,
                    title=item.title[:256],
                    summary=item.summary,
                    score=item.score,
                    payload={"url": item.url, **item.metadata},
                    expires_at=expires_at,
                )
                for item in result
            )
        return topics

    # --------------------------------------------------------- circuit breaker

    def _circuit_open_key(self, source: str) -> str:
        return f"trending:circuit:open:{source}"

    def _circuit_fail_key(self, source: str) -> str:
        return f"trending:circuit:fails:{source}"

    async def _is_circuit_open(self, source: str) -> bool:
        return await self._redis.exists(self._circuit_open_key(source)) > 0

    async def _record_circuit_success(self, source: str) -> None:
        await self._redis.delete(self._circuit_fail_key(source))

    async def _record_circuit_failure(self, source: str) -> None:
        fail_key = self._circuit_fail_key(source)
        failures = await self._redis.incr(fail_key)
        if failures == 1:
            await self._redis.expire(fail_key, _CIRCUIT_FAILURE_WINDOW_SECONDS)
        if failures >= _CIRCUIT_FAILURE_THRESHOLD:
            await self._redis.set(
                self._circuit_open_key(source), "1", ex=_CIRCUIT_COOLDOWN_SECONDS
            )
            log.warning(
                "trend_source_circuit_opened",
                source=source,
                failures=failures,
                cooldown_seconds=_CIRCUIT_COOLDOWN_SECONDS,
            )

    async def _check_external_rate_limit(self, source: str) -> bool:
        limiter = RedisRateLimiter(
            self._redis,
            f"ratelimit:external:{source}",
            _EXTERNAL_RATE_LIMIT_PER_MINUTE,
            _EXTERNAL_RATE_LIMIT_WINDOW_SECONDS,
        )
        return await limiter.allow()

    async def _discover_videos(
        self, niche: str, language: str, expires_at: datetime
    ) -> list[TrendingVideo]:
        if self._video_source is None:
            return []
        config = get_niche_config(niche)
        published_after = (
            (datetime.now(UTC) - timedelta(days=_DISCOVERY_WINDOW_DAYS))
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        search_results, chart_results = await asyncio.gather(
            self._video_source.search_top_videos(
                config.keywords,
                category_id=config.youtube_category_id,
                language=language,
                published_after=published_after,
                limit=25,
            ),
            self._video_source.search_top_videos(
                config.keywords, language=language, published_after=published_after, limit=25
            ),
            return_exceptions=True,
        )

        video_ids: list[str] = []
        for result in (search_results, chart_results):
            if isinstance(result, BaseException):
                log.warning("video_discovery_failed", niche=niche, error=str(result))
                continue
            for item in result:
                video_id = item.get("id", {}).get("videoId", "")
                if video_id and video_id not in video_ids:
                    video_ids.append(video_id)
        if not video_ids:
            return []

        try:
            details = await self._video_source.get_videos_stats(video_ids)
        except Exception as exc:
            log.warning("video_stats_failed", niche=niche, error=str(exc))
            return []

        videos = []
        for item in details:
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            published_raw = snippet.get("publishedAt", "")
            videos.append(
                TrendingVideo(
                    niche=niche,
                    language=language,
                    video_id=item["id"],
                    channel_id=snippet.get("channelId", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    title=snippet.get("title", "")[:256],
                    url=f"https://www.youtube.com/watch?v={item['id']}",
                    published_at=(
                        datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                        if published_raw
                        else None
                    ),
                    stats=stats,
                    score=float(stats.get("viewCount", 0)),
                    summary="",
                    context={"description": snippet.get("description", "")[:2000]},
                    expires_at=expires_at,
                )
            )
        videos.sort(key=lambda v: v.score, reverse=True)
        return videos[:_TOP_VIDEOS_KEPT]

    async def _summarize_videos(self, videos: list[TrendingVideo]) -> None:
        if self._llm is None or not videos:
            return
        targets = videos[:_VIDEOS_SUMMARIZED]

        system_prompt, template_version = _DEFAULT_SUMMARY_SYSTEM_PROMPT, None
        if self._db is not None:
            # Fetched once for the whole batch, not per video — the prompt
            # template can't change mid-batch, so refetching it per video
            # (even against a warm Redis cache) is pure waste.
            system_prompt, template_version = await prompts.get_active_prompt(
                self._db, self._redis, _SUMMARY_PROMPT_FEATURE, _DEFAULT_SUMMARY_SYSTEM_PROMPT
            )

        results = await asyncio.gather(
            *(self._summarize_one(v, system_prompt, template_version) for v in targets),
            return_exceptions=True,
        )
        for video, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("video_summary_failed", video_id=video.video_id, error=str(result))
                continue
            video.summary = result.summary
            video.context = {**video.context, **result.model_dump()}

    async def _summarize_one(
        self, video: TrendingVideo, system_prompt: str, template_version: int | None
    ) -> VideoContext:
        prompt = (
            f"Video title: {video.title}\n"
            f"Channel: {video.channel_title}\n"
            f"Published: {video.published_at}\n"
            f"Stats: {json.dumps(video.stats)}\n"
            f"Description: {video.context.get('description', '')}"
        )
        result = await self._llm.generate_structured(prompt, VideoContext, system=system_prompt)
        if self._db is not None:
            await prompts.log_invocation(
                self._db,
                feature=_SUMMARY_PROMPT_FEATURE,
                template_version=template_version,
                rendered_prompt=f"[system]\n{system_prompt}\n\n[user]\n{prompt}",
            )
        return result
