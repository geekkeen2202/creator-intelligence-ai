from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import channels
from app.modules.trending.niches import NICHE_MAP
from app.modules.trending.repository import TrendingRepository
from app.modules.trending.schemas import NicheContext, TrendingTopicRead, TrendingVideoRead
from app.modules.trending.service import TrendingService
from app.shared.cache import get_redis
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/trending", tags=["trending"])


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> TrendingService:
    return TrendingService(TrendingRepository(db), redis)


def validate_niche(niche: str = Query(...)) -> str:
    # Rejecting unknown niches here — not in the service — stops garbage
    # input (typos, scrapers) from ever reaching the ingest/circuit-breaker/
    # rate-limiter machinery, which is keyed per-source and shared across
    # every niche (see TrendingService._fetch_topics).
    if niche not in NICHE_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown niche: {niche!r}. Known: {sorted(NICHE_MAP)}",
        )
    return niche


@router.get("", response_model=list[TrendingTopicRead])
async def get_trending(
    service: Annotated[TrendingService, Depends(get_service)],
    niche: Annotated[str, Depends(validate_niche)],
    language: str = Query("en"),
    source: str | None = Query(None),
):
    return await service.get_trending(niche, language, source)


@router.get("/videos", response_model=list[TrendingVideoRead])
async def get_trending_videos(
    service: Annotated[TrendingService, Depends(get_service)],
    niche: Annotated[str, Depends(validate_niche)],
    language: str = Query("en"),
):
    return await service.get_trending_videos(niche, language)


@router.get("/for-channel/{channel_id}", response_model=NicheContext)
async def get_trending_for_channel(
    channel_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[TrendingService, Depends(get_service)],
    language: str = Query("en"),
):
    if not await channels.verify_ownership(db, channel_id, UUID(user.user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return await service.get_channel_context(channel_id, language)
