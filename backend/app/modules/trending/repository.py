from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trending.models import ChannelNicheAssignment, TrendingTopic, TrendingVideo


class TrendingRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def list_active(
        self, niche: str, language: str, source: str | None = None
    ) -> list[TrendingTopic]:
        query = (
            select(TrendingTopic)
            .where(
                TrendingTopic.niche == niche,
                TrendingTopic.language == language,
                TrendingTopic.expires_at > datetime.now(UTC),
                TrendingTopic.deleted_at.is_(None),
            )
            .order_by(TrendingTopic.score.desc())
        )
        if source is not None:
            query = query.where(TrendingTopic.source == source)
        result = await self._db.execute(query)
        return list(result.scalars().all())

    async def list_active_videos(self, niche: str, language: str) -> list[TrendingVideo]:
        result = await self._db.execute(
            select(TrendingVideo)
            .where(
                TrendingVideo.niche == niche,
                TrendingVideo.language == language,
                TrendingVideo.expires_at > datetime.now(UTC),
                TrendingVideo.deleted_at.is_(None),
            )
            .order_by(TrendingVideo.score.desc())
        )
        return list(result.scalars().all())

    async def has_ingested_batch(self, niche: str, language: str) -> bool:
        """Whether this (niche, language) pair has EVER had a topics batch —
        including expired/soft-deleted rows. Distinguishes "cold" niches (never
        ingested, needs an on-demand trigger) from "stale" ones (due for the
        next scheduled refresh, no special handling needed).
        """
        query = select(
            exists().where(TrendingTopic.niche == niche, TrendingTopic.language == language)
        )
        result = await self._db.execute(query)
        return bool(result.scalar())

    async def has_ingested_video_batch(self, niche: str, language: str) -> bool:
        """Same as has_ingested_batch but for trending_videos — kept separate
        because the two can genuinely diverge: video discovery is skipped
        whenever YOUTUBE_API_KEY is unset at ingest time, so a niche can have
        topics with zero videos ever written (not just stale — never run).
        """
        query = select(
            exists().where(TrendingVideo.niche == niche, TrendingVideo.language == language)
        )
        result = await self._db.execute(query)
        return bool(result.scalar())

    async def soft_delete_batch(self, niche: str, language: str) -> None:
        """Retire the previous ingest batch for this (niche, language) in both tables."""
        now = datetime.now(UTC)
        for model in (TrendingTopic, TrendingVideo):
            await self._db.execute(
                update(model)
                .where(
                    model.niche == niche,
                    model.language == language,
                    model.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )
        await self._db.commit()

    async def purge_soft_deleted(self, retention: timedelta) -> int:
        """Hard-deletes soft-deleted rows older than `retention` in both
        tables — without this, every daily refresh leaves its previous batch
        behind forever (soft_delete_batch only stamps deleted_at, never removes
        rows), so trending_topics/trending_videos grow unbounded.
        """
        cutoff = datetime.now(UTC) - retention
        purged = 0
        for model in (TrendingTopic, TrendingVideo):
            result = await self._db.execute(
                delete(model).where(model.deleted_at.is_not(None), model.deleted_at < cutoff)
            )
            purged += result.rowcount or 0
        await self._db.commit()
        return purged

    async def bulk_create(self, topics: list[TrendingTopic]) -> None:
        self._db.add_all(topics)
        await self._db.commit()

    async def bulk_create_videos(self, videos: list[TrendingVideo]) -> None:
        self._db.add_all(videos)
        await self._db.commit()

    async def get_niche_for_channel(self, channel_id: UUID) -> ChannelNicheAssignment | None:
        result = await self._db.execute(
            select(ChannelNicheAssignment).where(ChannelNicheAssignment.channel_id == channel_id)
        )
        return result.scalar_one_or_none()

    async def upsert_assignment(
        self, channel_id: UUID, niche: str, keywords: list[str], confidence: float
    ) -> None:
        stmt = insert(ChannelNicheAssignment).values(
            channel_id=channel_id, niche=niche, keywords=keywords, confidence=confidence
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ChannelNicheAssignment.channel_id],
            set_={"niche": niche, "keywords": keywords, "confidence": confidence},
        )
        await self._db.execute(stmt)
        await self._db.commit()
