import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.voice_dna_agent import PROMPT_VERSION, VoiceDNA
from app.config import get_settings
from app.modules import billing, channels, scripts
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

# Batch extraction (TechnicalDesign.md §5.1): analyze N transcripts at a time
# rather than one giant prompt — keeps each call small and reliable, and
# survives free-tier model context limits regardless of corpus size.
_BATCH_SIZE_TRANSCRIPTS = 8

# Free-tier LLM routing sometimes truncates very long prompts mid-response,
# producing invalid JSON instead of a parseable VoiceDNA object — cap the
# excerpt budget *per batch* so extraction stays reliable regardless of how
# long any individual transcript is.
_MAX_EXCERPT_CHARS_PER_BATCH = 6000

# Excerpt curation (TechnicalDesign.md §5.1 tail): cap on how many segments
# get stored as the profile's curated few-shot excerpts.
_MAX_CURATED_EXCERPTS = 12

_PROMPT_BLOCK_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # safety-net TTL; real
# invalidation happens on voice_profile.updated (ARCHITECTURE.md §10).

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")

# No audio-probing library in this dependency set, so Whisper minutes are
# estimated from transcript length at average speaking pace — documented
# approximation (TechnicalDesign.md §5.1/§8 rule 11 only requires cost be
# metered at source, not that the estimate be exact).
_ESTIMATED_WORDS_PER_MINUTE = 150


def _estimate_minutes(text: str) -> float:
    word_count = len(text.split())
    return word_count / _ESTIMATED_WORDS_PER_MINUTE


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


def _clean_text(raw: str) -> str:
    """Light normalization — collapse whitespace. `raw_text` keeps the
    verbatim source; `clean_text` is what gets segmented/extracted from
    (TechnicalDesign.md §3.1)."""
    return _WHITESPACE.sub(" ", raw).strip()


def _curate_excerpts(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Heuristic substitute for an LLM excerpt-curation pass (TechnicalDesign
    §5.1: "agent selects 8-12 most characteristic segments"): prioritize
    variety across segment types (hook/cta are short and characteristic by
    construction) and length within a type, capped at _MAX_CURATED_EXCERPTS.
    """
    by_type: dict[str, list[TranscriptSegment]] = defaultdict(list)
    for segment in segments:
        by_type[segment.segment_type].append(segment)

    curated: list[TranscriptSegment] = []
    for segment_type in ("hook", "cta", "body", "transition"):
        candidates = sorted(by_type.get(segment_type, []), key=lambda s: len(s.text), reverse=True)
        curated.extend(candidates[:4])
    return curated[:_MAX_CURATED_EXCERPTS]


def _consolidate(batch_results: list[VoiceDNA]) -> VoiceDNA:
    """Merges per-batch VoiceDNA analyses into one profile (TechnicalDesign
    §5.1 "consolidation pass"). Scalar dimensions take the most recent
    batch's read (batches are ordered most-recent-video-first); signature
    phrases are the recurring ones — a phrase seen in only one batch out of
    several is exactly the kind of one-off guess §6.2 says is worse to keep
    than to omit.
    """
    if len(batch_results) == 1:
        return batch_results[0]

    latest = batch_results[0]
    phrase_first_seen: dict[str, str] = {}
    phrase_counts: Counter[str] = Counter()
    for batch in batch_results:
        for phrase in batch.signature_phrases:
            key = phrase.strip().lower()
            if not key:
                continue
            phrase_counts[key] += 1
            phrase_first_seen.setdefault(key, phrase.strip())

    recurring_keys = [key for key, count in phrase_counts.items() if count >= 2]
    if not recurring_keys:
        recurring_keys = list(phrase_first_seen.keys())
    recurring = [phrase_first_seen[key] for key in recurring_keys][:15]

    return VoiceDNA(
        tone=latest.tone,
        pacing=latest.pacing,
        vocabulary_level=latest.vocabulary_level,
        hook_style=latest.hook_style,
        cta_style=latest.cta_style,
        signature_phrases=recurring,
    )


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
            text, quality_score, language, source = *captions, "captions"
        else:
            text, quality_score, language, source = await self._transcribe_via_whisper(video)

        clean = _clean_text(text)
        transcript = await self._repository.create_transcript(
            video_id=video.id,
            source=source,
            quality_score=quality_score,
            language_detected=language or None,
            raw_text=text,
            clean_text=clean,
        )
        segments = [
            TranscriptSegment(transcript_id=transcript.id, segment_type=seg_type, text=seg_text)
            for seg_type, seg_text in _segment_text(clean)
        ]
        if segments:
            await self._repository.bulk_create_segments(segments)

        if source == "whisper":
            await self._meter_whisper_usage(video.channel_id, clean)

        emit(
            TRANSCRIPTS_COMPLETED,
            {"channel_id": str(video.channel_id), "video_id": str(video.id)},
        )

    async def _meter_whisper_usage(self, channel_id: UUID, transcribed_text: str) -> None:
        """Cost metering at source (ARCHITECTURE.md §8 rule 11) — Whisper is
        the expensive fallback path, so "cost per onboarding" is meaningless
        without this."""
        user_id = await channels.get_owner_user_id(self._db, channel_id)
        if user_id is None:
            return
        minutes = _estimate_minutes(transcribed_text)
        cost = minutes * get_settings().whisper_price_per_minute
        await billing.record_usage(
            self._db,
            user_id,
            datetime.now(UTC).date(),
            feature="whisper_minutes",
            tokens=0,
            cost=cost,
        )

    async def _transcribe_via_whisper(self, video: Video) -> tuple[str, float, str, str]:
        if self._transcription is None:
            raise RuntimeError("No captions available and no TranscriptionPort configured")
        audio_bytes = await self._social.download_audio(video.external_video_id)
        if audio_bytes is None:
            raise RuntimeError(f"Could not obtain audio for video {video.external_video_id}")
        text, quality_score, language = await self._transcription.transcribe(audio_bytes)
        return text, quality_score, language, "whisper"

    # ------------------------------------------------------------- extraction

    async def _run_batches(
        self, channel_id: UUID, high_confidence: bool
    ) -> tuple[list[VoiceDNA], list[TranscriptSegment]]:
        transcript_groups = await self._repository.list_segments_by_transcript_for_channel(
            channel_id
        )
        batches = [
            transcript_groups[i : i + _BATCH_SIZE_TRANSCRIPTS]
            for i in range(0, len(transcript_groups), _BATCH_SIZE_TRANSCRIPTS)
        ]

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

        agent = get_voice_dna_agent()
        batch_results: list[VoiceDNA] = []
        all_segments: list[TranscriptSegment] = []
        for batch_index, batch in enumerate(batches):
            flat_segments = [s for transcript_segments in batch for s in transcript_segments]
            all_segments.extend(flat_segments)
            excerpt_text = "\n".join(f"[{s.segment_type}] {s.text}" for s in flat_segments)
            excerpt_text = excerpt_text[:_MAX_EXCERPT_CHARS_PER_BATCH]
            prompt = f"{instructions}\n\nExcerpts:\n{excerpt_text}"

            result = await agent.arun(prompt)
            if not isinstance(result.content, VoiceDNA):
                # One bad batch shouldn't sink the whole extraction — same
                # fan-out-isolation principle as per-video transcription
                # failures (ARCHITECTURE.md §11).
                log.warning(
                    "voice_dna_batch_unparseable", channel_id=str(channel_id), batch=batch_index
                )
                continue
            batch_results.append(result.content)

        return batch_results, all_segments

    async def extract_voice_profile(self, channel_id: UUID) -> None:
        transcript_count = await self._repository.count_transcripts_for_channel(channel_id)
        if transcript_count < _MIN_TRANSCRIPTS_FOR_EXTRACTION:
            log.info(
                "voice_profile_extraction_skipped_insufficient_transcripts",
                channel_id=str(channel_id),
                transcript_count=transcript_count,
            )
            return

        high_confidence = transcript_count >= _HIGH_CONFIDENCE_TRANSCRIPT_THRESHOLD
        batch_results, all_segments = await self._run_batches(channel_id, high_confidence)
        if not batch_results:
            raise RuntimeError(
                f"voice_dna agent returned unparseable output for every batch, channel {channel_id}"
            )

        voice_dna = _consolidate(batch_results)
        curated = _curate_excerpts(all_segments)
        confidence_label = "high" if high_confidence else "low"
        confidence = {field: confidence_label for field in VoiceDNA.model_fields}

        next_version = await self._repository.get_latest_version_number(channel_id) + 1
        profile = await self._repository.create_version(
            channel_id=channel_id,
            version=next_version,
            profile=voice_dna.model_dump(),
            confidence=confidence,
            excerpt_ids=[str(s.id) for s in curated],
            extraction_prompt_version=PROMPT_VERSION,
            source="initial",
        )
        await channels.set_current_voice_profile_id(self._db, channel_id, profile.id)
        await self._refresh_prompt_block_cache(channel_id, voice_dna)
        emit(
            VOICE_PROFILE_UPDATED,
            {"channel_id": str(channel_id), "version": next_version},
        )

    # ------------------------------------------------------------- refinement

    async def refine_profile(self, channel_id: UUID) -> bool:
        """Weekly refinement (TechnicalDesign.md §5.3). Returns True if a new
        version was written, False if skipped — creators with zero new
        signal since their last version get no empty version inserted.
        """
        latest = await self._repository.get_latest_version(channel_id)
        if latest is None:
            return False  # nothing to refine — extract_voice_profile owns v1

        feedback = await scripts.list_feedback_since(self._db, channel_id, latest.created_at)
        if not feedback:
            return False

        signal_lines = []
        for item in feedback:
            if item.rating is not None:
                detail = f" detail={item.rating_detail}" if item.rating_detail else ""
                signal_lines.append(f"- Rated {item.rating}/5{detail} — hook: \"{item.hook}\"")
            if item.final_text and item.final_text not in (item.hook, item.body, item.cta):
                signal_lines.append(
                    f"- Creator edited a generated script before use (hook: \"{item.hook}\")"
                )
        if not signal_lines:
            return False

        current_profile = VoiceDNA.model_validate(latest.profile)
        prompt = (
            "Current Voice DNA profile (JSON):\n"
            f"{current_profile.model_dump_json()}\n\n"
            "New creator feedback signals since this profile was set:\n"
            + "\n".join(signal_lines)
            + "\n\nAdjust the profile to better reflect these signals. Keep any "
            "dimension unchanged where the signals don't clearly suggest a change — "
            "never fabricate a change to look productive."
        )

        agent = get_voice_dna_agent()
        result = await agent.arun(prompt)
        if not isinstance(result.content, VoiceDNA):
            raise RuntimeError(
                f"voice_dna agent returned unparseable refinement output for channel {channel_id}"
            )
        voice_dna: VoiceDNA = result.content

        next_version = latest.version + 1
        profile = await self._repository.create_version(
            channel_id=channel_id,
            version=next_version,
            profile=voice_dna.model_dump(),
            confidence=latest.confidence,
            excerpt_ids=latest.excerpt_ids,
            extraction_prompt_version=PROMPT_VERSION,
            source="refinement",
        )
        await channels.set_current_voice_profile_id(self._db, channel_id, profile.id)
        await self._refresh_prompt_block_cache(channel_id, voice_dna)
        emit(
            VOICE_PROFILE_UPDATED,
            {"channel_id": str(channel_id), "version": next_version},
        )
        return True

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


async def get_ingestion_status(db: AsyncSession, channel_id: UUID) -> dict:
    """Public read for the onboarding progressive-status endpoint
    (TechnicalDesign.md §5.1) — how far ingest_channel -> transcribe_video ->
    extract_voice_profile has gotten for this channel."""
    repository = VoiceProfileRepository(db)
    videos_selected = await repository.count_selected_videos(channel_id)
    transcripts_completed = await repository.count_transcripts_for_channel(channel_id)
    profile_id = await channels.get_current_voice_profile_id(db, channel_id)
    voice_profile = await repository.get_by_id(profile_id) if profile_id else None
    return {
        "channel_id": channel_id,
        "videos_selected": videos_selected,
        "transcripts_completed": transcripts_completed,
        "voice_profile_ready": voice_profile is not None,
        "voice_profile_version": voice_profile.version if voice_profile else None,
    }
