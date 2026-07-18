import asyncio

import praw

from app.config import get_settings
from app.modules.trending.niches import get_niche_config
from app.shared.ports.trend_source_port import RawTrendItem


class RedditAdapter:
    """Implements TrendSourcePort via praw (Reddit API)."""

    source = "reddit"

    def __init__(self) -> None:
        settings = get_settings()
        self._client_id = settings.reddit_client_id
        self._client_secret = settings.reddit_client_secret
        self._user_agent = settings.reddit_user_agent

    def is_enabled(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _fetch_sync(self, niche: str) -> list[RawTrendItem]:
        config = get_niche_config(niche)
        reddit = praw.Reddit(
            client_id=self._client_id,
            client_secret=self._client_secret,
            user_agent=self._user_agent,
        )
        # "sub1+sub2" multireddit syntax fetches several subreddits in one listing.
        subreddit = reddit.subreddit("+".join(config.subreddits))
        return [
            RawTrendItem(
                title=post.title,
                summary=(post.selftext or "")[:500],
                url=f"https://www.reddit.com{post.permalink}",
                score=float(post.score),
                metadata={
                    "post_id": post.id,
                    "subreddit": post.subreddit.display_name,
                    "num_comments": post.num_comments,
                    "external_url": post.url,
                },
            )
            for post in subreddit.hot(limit=25)
            if not post.stickied
        ]

    async def fetch_trending_topics(self, niche: str, language: str) -> list[RawTrendItem]:
        return await asyncio.to_thread(self._fetch_sync, niche)
