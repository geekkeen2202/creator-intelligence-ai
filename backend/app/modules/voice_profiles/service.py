import re
from datetime import datetime
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.voice_dna_agent import PROMPT_VERSION, VoiceDNA
from app.modules import channels
from app.modules.voice_profiles.agents import get_voice_dna_agent
from app.modules.voice_profiles.events import TRANSCRIPTS_COMPLETED, VOICE_PROFILE_UPDATED
from app.modules.voice_profiles.models import TranscriptSegment, Video
from app.modules.voice_profiles.repository import VoiceProfileRepository
from app.shared.events import emit
from app.shared.ports.social_platform_port import SocialPlatformPort
from app.shared.ports.transcription_port import TranscriptionPort

log = structlog.get_logger(__name__)

# How many of a channel's most recent videos are candidates for Voice DNA
# extraction (fan-out isolation — a few failed transcriptions are fine, see
# ARCHITECTURE.md §11).
_VIDEOS_PER_CHANNEL = 15

# Minimum usable transcripts before extraction is attempted at all — below
# this, there isn't enough signal for even a low-confidence profile.
_MIN_TRANSCRIPTS_FOR_EXTRACTION = 2

# Below this transcript count, confidence is "low" and the prompt explicitly
# forbids fabricating catchphrases (ARCHITECTURE.md §7).
_HIGH_CONFIDENCE_TRANSCRIPT_THRESHOLD = 5

# Below this caption/whisper quality score, fall back captions->whisper.
_MIN_CAPTION_QUALITY = 0.5

# Free-tier LLM routing sometimes truncates very long prompts mid-response,
# producing invalid JSON instead of a parseable VoiceDNA object — cap the
# excerpt budget so extraction stays reliable regardless of how much
# transcript text a channel has accumulated.
_MAX_EXCERPT_CHARS = 6000

_PROMPT_BLOCK_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # safety-net TTL; real
# invalidation happens on voice_profile.updated (ARCHITECTURE.md §10).

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _segment_text(text: str) -> list[tuple[str, str]]:
    """Cheap heuristic split into hook/body/cta — first sentence is the
    hook, last is the cta, everything between is body. Good enough for
    Voice DNA excerpt curation; not a claim of semantic accuracy.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        return []
    if len(sentences) == 1:
        return [("body", sentences[0])]
    body = " ".join(sentences[1:-1]) if len(sentences) > 2 else ""
    segments = [("hook", sentences[0])]
    if body:
        segments.append(("body", body))
    segments.append(("cta", sentences[-1]))
    return segments


class VoiceProfileService:
    def __init__(
        self,
        repository: VoiceProfileRepository,
        db: AsyncSession,
        redis: Redis | None,
        *,
        social: SocialPlatformPort,
        transcription: TranscriptionPort | None = None,
    ):
        self._repository = repository
        self._db = db
        self._redis = redis
        self._social = social
        self._transcription = transcription

    # ----------------------------------------------------------------- ingest

    async def ingest_channel(self, channel_id: UUID, external_channel_id: str) -> list[UUID]:
        """Writes `videos` rows for the channel's recent uploads and returns
        their ids for the caller to fan out transcribe_video tasks over.
        """
        raw_videos = await self._social.get_recent_videos(
            external_channel_id, limit=_VIDEOS_PER_CHANNEL
        )
        video_ids: list[UUID] = []
        for item in raw_videos:
            snippet = item.get("snippet", {})
            external_video_id = item.get("id", {}).get("videoId", "")
            if not external_video_id:
                continue
            published_raw = snippet.get("publishedAt", "")
            video = await self._repository.create_video(
                channel_id=channel_id,
                external_video_id=external_video_id,
                title=snippet.get("title", "")[:256],
                published_at=(
                    datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                    if published_raw
                    else None
                ),
                selected_for_dna=True,
            )
            video_ids.append(video.id)
        return video_ids

    # ------------------------------------------------------------- transcribe

    async def transcribe_video(self, video_id: UUID) -> None:
        video = await self._repository.get_video(video_id)
        if video is None:
            return

        captions = await self._social.get_captions(video.external_video_id)
        if captions is not None and captions[1] >= _MIN_CAPTION_QUALITY:
            text, quality_score, source = *captions, "captions"
        else:
            text, quality_score, source = await self._transcribe_via_whisper(video)

        transcript = await self._repository.create_transcript(
            video_id=video.id, source=source, quality_score=quality_score
        )
        segments = [
            TranscriptSegment(transcript_id=transcript.id, segment_type=seg_type, text=seg_text)
            for seg_type, seg_text in _segment_text(text)
        ]
        if segments:
            await self._repository.bulk_create_segments(segments)

        emit(
            TRANSCRIPTS_COMPLETED,
            {"channel_id": str(video.channel_id), "video_id": str(video.id)},
        )

    async def _transcribe_via_whisper(self, video: Video) -> tuple[str, float, str]:
        if self._transcription is None:
            raise RuntimeError("No captions available and no TranscriptionPort configured")
        audio_bytes = await self._social.download_audio(video.external_video_id)
        if audio_bytes is None:
            raise RuntimeError(f"Could not obtain audio for video {video.external_video_id}")
        text, quality_score = await self._transcription.transcribe(audio_bytes)
        return text, quality_score, "whisper"

    # ------------------------------------------------------------- extraction

    async def extract_voice_profile(self, channel_id: UUID) -> None:
        transcript_count = await self._repository.count_transcripts_for_channel(channel_id)
        if transcript_count < _MIN_TRANSCRIPTS_FOR_EXTRACTION:
            log.info(
                "voice_profile_extraction_skipped_insufficient_transcripts",
                channel_id=str(channel_id),
                transcript_count=transcript_count,
            )
            return

        segments = await self._repository.list_segments_for_channel(channel_id)
        high_confidence = transcript_count >= _HIGH_CONFIDENCE_TRANSCRIPT_THRESHOLD
        confidence_label = "high" if high_confidence else "low"

        excerpt_text = "\n".join(f"[{s.segment_type}] {s.text}" for s in segments)
        excerpt_text = excerpt_text[:_MAX_EXCERPT_CHARS]
        instructions = (
            "Analyze these excerpts from a creator's past video transcripts and "
            "extract their Voice DNA (tone, pacing, vocabulary level, hook style, "
            "CTA style, and signature phrases)."
        )
        if not high_confidence:
            instructions += (
                " Only a small sample is available — do not fabricate signature "
                "phrases or overly specific patterns; prefer general, neutral "
                "descriptions for any dimension the sample doesn't clearly support."
            )
        prompt = f"{instructions}\n\nExcerpts:\n{excerpt_text}"

        agent = get_voice_dna_agent()
        result = await agent.arun(prompt)
        if not isinstance(result.content, VoiceDNA):
            # Free-tier LLM routing occasionally returns malformed/truncated
            # JSON that agno can't parse into VoiceDNA — surface a clear
            # error so Celery's retry (and its logs) are meaningful instead
            # of a confusing AttributeError deeper in this function.
            raise RuntimeError(
                f"voice_dna agent returned unparseable output for channel {channel_id}"
            )
        voice_dna: VoiceDNA = result.content

        confidence = {field: confidence_label for field in VoiceDNA.model_fields}
        next_version = await self._repository.get_latest_version_number(channel_id) + 1
        profile = await self._repository.create_version(
            channel_id=channel_id,
            version=next_version,
            profile=voice_dna.model_dump(),
            confidence=confidence,
            excerpt_ids=[str(s.id) for s in segments],
            extraction_prompt_version=PROMPT_VERSION,
        )
        await channels.set_current_voice_profile_id(self._db, channel_id, profile.id)
        await self._refresh_prompt_block_cache(channel_id, voice_dna)
        emit(
            VOICE_PROFILE_UPDATED,
            {"channel_id": str(channel_id), "version": next_version},
        )

    # --------------------------------------------------------------- prompt block

    async def _refresh_prompt_block_cache(self, channel_id: UUID, voice_dna: VoiceDNA) -> None:
        await self._redis.set(
            f"promptblock:{channel_id}",
            _format_prompt_block(voice_dna),
            ex=_PROMPT_BLOCK_CACHE_TTL_SECONDS,
        )


_NOT_YET_AVAILABLE = "Voice DNA: not yet available"


def _format_prompt_block(voice_dna: VoiceDNA) -> str:
    lines = ["Voice DNA:"]
    for field, value in voice_dna.model_dump().items():
        if isinstance(value, list):
            value = ", ".join(value) or "none"
        lines.append(f"  {field}: {value}")
    return "\n".join(lines)


async def get_prompt_block(db: AsyncSession, redis: Redis, channel_id: UUID) -> str:
    """Public read used by scripts/thumbnails — cache-first, falling back to
    the current voice_profiles row, falling back to "not yet available"
    (graceful degradation, never blocks generation on Voice DNA).
    """
    cache_key = f"promptblock:{channel_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return cached

    profile_id = await channels.get_current_voice_profile_id(db, channel_id)
    if profile_id is None:
        return _NOT_YET_AVAILABLE

    voice_profile = await VoiceProfileRepository(db).get_by_id(profile_id)
    if voice_profile is None:
        return _NOT_YET_AVAILABLE

    try:
        voice_dna = VoiceDNA.model_validate(voice_profile.profile)
    except Exception as exc:
        log.warning(
            "voice_dna_shape_mismatch", channel_id=str(channel_id), error=str(exc)
        )
        return _NOT_YET_AVAILABLE

    block = _format_prompt_block(voice_dna)
    await redis.set(cache_key, block, ex=_PROMPT_BLOCK_CACHE_TTL_SECONDS)
    return block


async def get_current(db: AsyncSession, channel_id: UUID):
    """Read used by the router to expose onboarding/profile status."""
    profile_id = await channels.get_current_voice_profile_id(db, channel_id)
    if profile_id is None:
        return None
    return await VoiceProfileRepository(db).get_by_id(profile_id)


async def get_current_profile_version(db: AsyncSession, channel_id: UUID) -> int | None:
    """Public read used by scripts for provenance stamping (§8 rule 9)."""
    profile_id = await channels.get_current_voice_profile_id(db, channel_id)
    if profile_id is None:
        return None
    voice_profile = await VoiceProfileRepository(db).get_by_id(profile_id)
    return voice_profile.version if voice_profile else None
