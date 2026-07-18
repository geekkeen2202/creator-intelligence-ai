from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
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
        video = Video(
            channel_id=channel_id,
            external_video_id=external_video_id,
            title=title,
            published_at=published_at,
            selected_for_dna=selected_for_dna,
        )
        self._db.add(video)
        await self._db.commit()
        await self._db.refresh(video)
        return video

    async def list_videos_for_channel(self, channel_id: UUID) -> list[Video]:
        result = await self._db.execute(
            select(Video).where(
                Video.channel_id == channel_id, Video.deleted_at.is_(None)
            )
        )
        return list(result.scalars().all())

    async def get_video(self, video_id: UUID) -> Video | None:
        return await self._db.get(Video, video_id)

    # ------------------------------------------------------------- transcripts

    async def create_transcript(
        self, *, video_id: UUID, source: str, quality_score: float
    ) -> Transcript:
        transcript = Transcript(video_id=video_id, source=source, quality_score=quality_score)
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

    async def create_version(
        self,
        *,
        channel_id: UUID,
        version: int,
        profile: dict,
        confidence: dict,
        excerpt_ids: list[str],
        extraction_prompt_version: str,
    ) -> VoiceProfile:
        voice_profile = VoiceProfile(
            channel_id=channel_id,
            version=version,
            profile=profile,
            confidence=confidence,
            excerpt_ids=excerpt_ids,
            extraction_prompt_version=extraction_prompt_version,
        )
        self._db.add(voice_profile)
        await self._db.commit()
        await self._db.refresh(voice_profile)
        return voice_profile
