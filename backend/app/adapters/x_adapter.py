from app.config import get_settings
from app.shared.ports.trend_source_port import RawTrendItem


class XAdapter:
    """Implements TrendSourcePort for X (Twitter).

    Disabled until X_BEARER_TOKEN is configured — X's search/trends API
    requires a paid plan. Enabling it is config-only: set the token and this
    source joins the fan-out automatically.
    """

    source = "x"

    def __init__(self) -> None:
        self._bearer_token = get_settings().x_bearer_token

    def is_enabled(self) -> bool:
        return bool(self._bearer_token)

    async def fetch_trending_topics(self, niche: str, language: str) -> list[RawTrendItem]:
        if not self.is_enabled():
            return []
        # TODO: call GET https://api.x.com/2/tweets/search/recent with niche
        # keywords once a paid bearer token is available.
        raise NotImplementedError("X trends integration pending paid API access")
