"""Static niche configuration mapping each niche to platform-specific selectors.

Each platform speaks a different language: YouTube wants numeric category IDs,
Reddit wants subreddits, Google Trends / YouTube search want keywords.
"""

from pydantic import BaseModel


class NicheConfig(BaseModel):
    youtube_category_id: str
    subreddits: list[str]
    keywords: list[str]


NICHE_MAP: dict[str, NicheConfig] = {
    "technology": NicheConfig(
        youtube_category_id="28",  # Science & Technology
        subreddits=["technology", "gadgets", "india", "developersIndia"],
        keywords=["technology", "AI", "smartphone", "gadgets"],
    ),
    "finance": NicheConfig(
        youtube_category_id="25",  # News & Politics (closest chart category)
        subreddits=["IndiaInvestments", "personalfinanceindia", "StockMarketIndia"],
        keywords=["stock market", "personal finance", "investing", "mutual funds"],
    ),
    "entertainment": NicheConfig(
        youtube_category_id="24",  # Entertainment
        subreddits=["bollywood", "BollyBlindsNGossip", "entertainment"],
        keywords=["bollywood", "movies", "web series", "celebrity"],
    ),
    "education": NicheConfig(
        youtube_category_id="27",  # Education
        subreddits=["Indian_Academia", "JEENEETards", "UPSC"],
        keywords=["study tips", "exam preparation", "online courses", "career"],
    ),
    "gaming": NicheConfig(
        youtube_category_id="20",  # Gaming
        subreddits=["IndianGaming", "gaming", "BGMI"],
        keywords=["gaming", "BGMI", "esports", "game review"],
    ),
    # Fallback bucket for channels that don't classify into any curated niche
    # above (e.g. no metadata yet, or genuinely cross-cutting content).
    "general": NicheConfig(
        youtube_category_id="22",  # People & Blogs
        subreddits=["india", "AskIndia"],
        keywords=["vlog", "lifestyle", "daily", "trending"],
    ),
}


def get_niche_config(niche: str) -> NicheConfig:
    try:
        return NICHE_MAP[niche]
    except KeyError as exc:
        raise ValueError(f"Unknown niche: {niche!r}. Known: {sorted(NICHE_MAP)}") from exc
