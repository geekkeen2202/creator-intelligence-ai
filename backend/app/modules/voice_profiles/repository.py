from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.voice_profiles.models import Transcript, TranscriptSegment, Video, VoiceProfile


class VoiceProfileRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    # ------------------------------------------------------------------ videos

    async def create_video(
        self,
        *,
        channel_id: UUID,
        external_video_id: str,
        title: str,
        published_at: datetime | None,
        selected_for_dna: bool = False,
    ) -> Video:
        # ON CONFLICT DO NOTHING: ingest_channel retries (max_retries=3, see
        # tasks/channels_tasks.py) must not duplicate a video already written
        # by an earlier partial attempt (ARCHITECTURE.md §11 idempotency).
        stmt = (
            insert(Video)
            .values(
                channel_id=channel_id,
                external_video_id=external_video_id,
                title=title,
                published_at=published_at,
                selected_for_dna=selected_for_dna,
            )
            .on_conflict_do_nothing(index_elements=[Video.channel_id, Video.external_video_id])
            .returning(Video)
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        video = result.scalar_one_or_none()
        if video is not None:
            return video
        # Conflict swallowed the INSERT — the row already exists, fetch it.
        existing = await self._db.execute(
            select(Video).where(
                Video.channel_id == channel_id, Video.external_video_id == external_video_id
            )
        )
        return existing.scalar_one()

    async def list_videos_for_channel(self, channel_id: UUID) -> list[Video]:
        result = await self._db.execute(
            select(Video).where(
                Video.channel_id == channel_id, Video.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())

    async def get_video(self, video_id: UUID) -> Video | None:
        return await self._db.get(Video, video_id)

    async def count_selected_videos(self, channel_id: UUID) -> int:
        result = await self._db.execute(
            select(func.count(Video.id)).where(
                Video.channel_id == channel_id, Video.selected_for_dna.is_(True)
            )
        )
        return result.scalar_one()

    # ------------------------------------------------------------- transcripts

    async def create_transcript(
        self,
        *,
        video_id: UUID,
        source: str,
        quality_score: float,
        language_detected: str | None = None,
        raw_text: str = "",
        clean_text: str = "",
    ) -> Transcript:
        transcript = Transcript(
            video_id=video_id,
            source=source,
            quality_score=quality_score,
            language_detected=language_detected,
            raw_text=raw_text,
            clean_text=clean_text,
        )
        self._db.add(transcript)
        await self._db.commit()
        await self._db.refresh(transcript)
        return transcript

    async def bulk_create_segments(self, segments: list[TranscriptSegment]) -> None:
        self._db.add_all(segments)
        await self._db.commit()

    async def list_segments_for_channel(
        self, channel_id: UUID, limit: int = 200
    ) -> list[TranscriptSegment]:
        """Curated excerpts for Voice DNA extraction — every transcript
        belonging to a video on this channel, most recent videos first.
        """
        result = await self._db.execute(
            select(TranscriptSegment)
            .join(Transcript, TranscriptSegment.transcript_id == Transcript.id)
            .join(Video, Transcript.video_id == Video.id)
            .where(Video.channel_id == channel_id, TranscriptSegment.deleted_at.is_(None))
            .order_by(Video.published_at.desc().nullslast())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_segments_by_transcript_for_channel(
        self, channel_id: UUID
    ) -> list[list[TranscriptSegment]]:
        """Same rows as list_segments_for_channel, but grouped per transcript
        (most recent video first) — the unit batch extraction (§5.1) chunks
        over, since a batch is "N transcripts," not "N segments."
        """
        result = await self._db.execute(
            select(TranscriptSegment)
            .join(Transcript, TranscriptSegment.transcript_id == Transcript.id)
            .join(Video, Transcript.video_id == Video.id)
            .where(Video.channel_id == channel_id, TranscriptSegment.deleted_at.is_(None))
            .order_by(Video.published_at.desc().nullslast(), TranscriptSegment.created_at)
        )
        segments = list(result.scalars().all())
        by_transcript: dict[UUID, list[TranscriptSegment]] = {}
        order: list[UUID] = []
        for segment in segments:
            if segment.transcript_id not in by_transcript:
                by_transcript[segment.transcript_id] = []
                order.append(segment.transcript_id)
            by_transcript[segment.transcript_id].append(segment)
        return [by_transcript[t] for t in order]

    async def list_segments_by_ids(self, segment_ids: list[UUID]) -> list[TranscriptSegment]:
        """Fetches curated excerpt segments by id, preserving no particular
        order (callers order by their own priority — see
        _select_prompt_excerpts in voice_profiles/service.py)."""
        if not segment_ids:
            return []
        result = await self._db.execute(
            select(TranscriptSegment).where(
                TranscriptSegment.id.in_(segment_ids), TranscriptSegment.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())

    async def count_transcripts_for_channel(self, channel_id: UUID) -> int:
        result = await self._db.execute(
            select(func.count(Transcript.id))
            .join(Video, Transcript.video_id == Video.id)
            .where(Video.channel_id == channel_id)
        )
        return result.scalar_one()

    # ------------------------------------------------------------ voice profiles

    async def get_by_id(self, voice_profile_id: UUID) -> VoiceProfile | None:
        return await self._db.get(VoiceProfile, voice_profile_id)

    async def get_latest_version_number(self, channel_id: UUID) -> int:
        result = await self._db.execute(
            select(func.max(VoiceProfile.version)).where(VoiceProfile.channel_id == channel_id)
        )
        return result.scalar_one() or 0

    async def get_latest_version(self, channel_id: UUID) -> VoiceProfile | None:
        result = await self._db.execute(
            select(VoiceProfile)
            .where(VoiceProfile.channel_id == channel_id)
            .order_by(VoiceProfile.version.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def create_version(
        self,
        *,
        channel_id: UUID,
        version: int,
        profile: dict,
        confidence: dict,
        excerpt_ids: list[str],
        extraction_prompt_version: str,
        source: str = "initial",
    ) -> VoiceProfile:
        voice_profile = VoiceProfile(
            channel_id=channel_id,
            version=version,
            profile=profile,
            confidence=confidence,
            excerpt_ids=excerpt_ids,
            extraction_prompt_version=extraction_prompt_version,
            source=source,
        )
        self._db.add(voice_profile)
        await self._db.commit()
        await self._db.refresh(voice_profile)
        return voice_profile
