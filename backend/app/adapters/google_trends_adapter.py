import asyncio

import structlog
from pytrends.exceptions import TooManyRequestsError
from pytrends.request import TrendReq
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.modules.trending.niches import get_niche_config
from app.shared.ports.trend_source_port import RawTrendItem

log = structlog.get_logger(__name__)


class GoogleTrendsAdapter:
    """Implements TrendSourcePort via pytrends (unofficial Google Trends API).

    Uses related_queries on the niche keywords — rising/top queries are an
    actual "what people search right now" signal, unlike interest_over_time.

    pytrends scrapes trends.google.com (no official API/key) and Google
    rate-limits aggressively per IP. Retries with backoff smooth over
    transient 429s; a sustained block still raises, and the caller
    (TrendingService._fetch_topics) isolates that failure per source.

    Retries are implemented with tenacity rather than pytrends' own
    retries=/backoff_factor= constructor args — those pass method_whitelist=
    to urllib3's Retry(), a kwarg removed in urllib3>=2.0, so pytrends'
    built-in retry path hard-crashes on current urllib3 (tenacity wraps the
    call instead and never touches that code path).
    """

    source = "google_trends"

    def is_enabled(self) -> bool:
        return True  # no API key required

    @retry(
        retry=retry_if_exception_type(TooManyRequestsError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=10),
        reraise=True,
    )
    def _fetch_sync(self, niche: str, language: str) -> list[RawTrendItem]:
        config = get_niche_config(niche)
        pytrends = TrendReq(hl=language, tz=330)
        try:
            # pytrends allows at most 5 keywords per payload.
            pytrends.build_payload(config.keywords[:5], geo="IN", timeframe="now 7-d")
            related = pytrends.related_queries()
        except TooManyRequestsError:
            log.warning("google_trends_rate_limited", niche=niche, language=language)
            raise

        items: list[RawTrendItem] = []
        seen: set[str] = set()
        for keyword, tables in related.items():
            for kind in ("rising", "top"):
                df = tables.get(kind)
                if df is None:
                    continue
                for row in df.itertuples():
                    query = str(row.query)
                    if query.lower() in seen:
                        continue
                    seen.add(query.lower())
                    items.append(
                        RawTrendItem(
                            title=query,
                            score=float(row.value),
                            metadata={"keyword": keyword, "kind": kind},
                        )
                    )
        return items

    async def fetch_trending_topics(self, niche: str, language: str) -> list[RawTrendItem]:
        return await asyncio.to_thread(self._fetch_sync, niche, language)
