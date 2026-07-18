from app.config import get_settings
from app.shared.ports.trend_source_port import RawTrendItem


class FacebookAdapter:
    """Implements TrendSourcePort for Facebook.

    Disabled until FACEBOOK_ACCESS_TOKEN is configured — Meta retired its
    public trending API, so this will target the Graph API (owned pages /
    content library) when access exists. Enabling it is config-only.
    """

    source = "facebook"

    def __init__(self) -> None:
        self._access_token = get_settings().facebook_access_token

    def is_enabled(self) -> bool:
        return bool(self._access_token)

    async def fetch_trending_topics(self, niche: str, language: str) -> list[RawTrendItem]:
        if not self.is_enabled():
            return []
        # TODO: implement via Graph API when an access token with the right
        # permissions is available.
        raise NotImplementedError("Facebook trends integration pending Graph API access")
