import difflib
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.voice_dna_agent import PROMPT_VERSION, VoiceDNA
from app.config import get_settings
from app.modules import billing, channels, prompts, scripts
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

# Per-dimension confidence (TechnicalDesign.md §6.2): different dimensions
# become trustworthy at different corpus sizes — tone/vocabulary read off
# 1-2 videos, hook/cta style need 3-5, catchphrases need 10+ AND must
# actually recur across batches (see _consolidate). Each entry is
# (medium_at, high_at) in transcript count.
_CONFIDENCE_THRESHOLDS: dict[str, tuple[int, int]] = {
    "tone": (1, 2),
    "vocabulary_level": (1, 2),
    "pacing": (2, 3),
    "hook_style": (3, 5),
    "cta_style": (3, 5),
    "signature_phrases": (6, 10),
}

# Catchphrases are the dimension most likely to be a hallucinated guess from
# a thin corpus, so the extraction prompt's anti-fabrication instruction is
# gated on the same bar as signature_phrases reaching "high" confidence —
# one source of truth instead of a separate flat threshold.
_ANTI_FABRICATION_THRESHOLD = _CONFIDENCE_THRESHOLDS["signature_phrases"][1]

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

# Of the curated excerpts, how many actually get injected as verbatim
# few-shot examples into the script-generation prompt (TechnicalDesign.md
# §6.1 "profile and excerpts, always both") — kept small to bound prompt
# token cost regardless of how many are curated/stored.
_MAX_PROMPT_EXCERPTS = 5
_MAX_EXCERPT_CHARS_IN_PROMPT = 300
_PROMPT_EXCERPT_TYPE_PRIORITY = ("hook", "cta", "body", "transition")

_PROMPT_BLOCK_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # safety-net TTL; real
# invalidation happens on voice_profile.updated (ARCHITECTURE.md §10).

# Refinement signals (TechnicalDesign.md §5.3): a compact word-level diff
# between what was generated and what the creator actually kept, capped so
# one heavily-rewritten script can't blow out the refinement prompt.
_MAX_EDIT_DIFF_CHARS = 400

# Outcome (view-count) signals only get surfaced to refinement once there's
# enough published scripts to name distinct top/bottom performers — below
# this, "top 2" and "bottom 2" would overlap and the signal is noise rather
# than data (same "worse to guess than omit" principle as elsewhere in this
# module).
_MIN_OUTCOMES_FOR_PERFORMANCE_SIGNAL = 4
_OUTCOME_SIGNALS_PER_SIDE = 2

# DB-editable prompt instructions (prompts module) — these used to be
# hardcoded string literals here. The text below is now only the fallback
# used if no active row exists yet for the feature (graceful degradation,
# same principle as voice_profiles.get_prompt_block's own fallback).
_EXTRACTION_PROMPT_FEATURE = "voice_dna_extraction"
_DEFAULT_EXTRACTION_INSTRUCTIONS = (
    "Analyze these excerpts from a creator's past video transcripts and "
    "extract their Voice DNA (tone, pacing, vocabulary level, hook style, "
    "CTA style, and signature phrases)."
)

_REFINEMENT_PROMPT_FEATURE = "voice_dna_refinement"
_DEFAULT_REFINEMENT_INSTRUCTIONS = (
    "Adjust the profile to better reflect these signals. Keep any "
    "dimension unchanged where the signals don't clearly suggest a change — "
    "never fabricate a change to look productive."
)

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


def _select_prompt_excerpts(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Narrows curated excerpts down to the handful actually injected as
    verbatim examples in the script prompt, preferring hook/cta segments
    (most characteristic of "voice") over body/transition."""
    by_type: dict[str, list[TranscriptSegment]] = defaultdict(list)
    for segment in segments:
        by_type[segment.segment_type].append(segment)
    ordered: list[TranscriptSegment] = []
    for segment_type in _PROMPT_EXCERPT_TYPE_PRIORITY:
        ordered.extend(by_type.get(segment_type, []))
    return ordered[:_MAX_PROMPT_EXCERPTS]


def _format_excerpt_lines(segments: list[TranscriptSegment]) -> list[str]:
    lines = []
    for segment in segments:
        text = segment.text.strip()
        if len(text) > _MAX_EXCERPT_CHARS_IN_PROMPT:
            text = text[:_MAX_EXCERPT_CHARS_IN_PROMPT].rstrip() + "..."
        lines.append(f'  [{segment.segment_type}] "{text}"')
    return lines


def _consolidate(batch_results: list[VoiceDNA]) -> tuple[VoiceDNA, bool]:
    """Merges per-batch VoiceDNA analyses into one profile (TechnicalDesign
    §5.1 "consolidation pass"). Scalar dimensions take the most recent
    batch's read (batches are ordered most-recent-video-first); signature
    phrases are the recurring ones — a phrase seen in only one batch out of
    several is exactly the kind of one-off guess §6.2 says is worse to keep
    than to omit.

    Returns (profile, phrases_confirmed) — phrases_confirmed is True only if
    at least one phrase actually recurred across >=2 batches, i.e. the
    corpus cross-checked itself. A single-batch extraction or a batch set
    where nothing recurred means every phrase is still a one-shot guess, so
    the caller must not report signature_phrases confidence above "low"
    regardless of transcript count.
    """
    if len(batch_results) == 1:
        return batch_results[0], False

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
    phrases_confirmed = bool(recurring_keys)
    if not recurring_keys:
        recurring_keys = list(phrase_first_seen.keys())
    recurring = [phrase_first_seen[key] for key in recurring_keys][:15]

    voice_dna = VoiceDNA(
        tone=latest.tone,
        pacing=latest.pacing,
        vocabulary_level=latest.vocabulary_level,
        hook_style=latest.hook_style,
        cta_style=latest.cta_style,
        signature_phrases=recurring,
    )
    return voice_dna, phrases_confirmed


def _confidence_tier(transcript_count: int, medium_at: int, high_at: int) -> str:
    if transcript_count >= high_at:
        return "high"
    if transcript_count >= medium_at:
        return "medium"
    return "low"


def _compute_confidence(transcript_count: int, phrases_confirmed: bool) -> dict[str, str]:
    confidence = {}
    for field, (medium_at, high_at) in _CONFIDENCE_THRESHOLDS.items():
        tier = _confidence_tier(transcript_count, medium_at, high_at)
        if field == "signature_phrases" and not phrases_confirmed:
            tier = "low"
        confidence[field] = tier
    return confidence


def _refine_signature_phrase_confidence(
    prior_tier: str, prior_phrases: list[str], new_phrases: list[str]
) -> str:
    """Refinement makes a single LLM call with no cross-batch recurrence
    check, so any signature phrase it introduces that wasn't already in the
    prior (corpus-confirmed) profile is an unconfirmed guess — confidence
    can only be carried forward, never upgraded, by a refinement pass.
    """
    prior_set = {p.strip().lower() for p in prior_phrases}
    new_set = {p.strip().lower() for p in new_phrases}
    if new_set <= prior_set:
        return prior_tier
    return "low"


def _summarize_edit_diff(generated_text: str, final_text: str) -> str:
    """Compact word-level diff between the generated script and the
    creator's final edited version — the richest refinement signal
    (TechnicalDesign.md §5.3): it shows exactly what a human changed, not
    just that something did.
    """
    gen_words = generated_text.split()
    final_words = final_text.split()
    matcher = difflib.SequenceMatcher(None, gen_words, final_words)
    parts = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        removed = " ".join(gen_words[i1:i2])
        added = " ".join(final_words[j1:j2])
        if removed:
            parts.append(f'-"{removed}"')
        if added:
            parts.append(f'+"{added}"')
    summary = " ".join(parts).strip()
    if not summary:
        return "(only whitespace/formatting changed)"
    if len(summary) > _MAX_EDIT_DIFF_CHARS:
        summary = summary[:_MAX_EDIT_DIFF_CHARS].rstrip() + "..."
    return summary


def _build_feedback_signal_lines(feedback: list, outcomes: list) -> list[str]:
    """Assembles refinement signal lines from two independent sources:
    creator feedback (ratings + edits) and published-video performance
    (view counts). Either can be empty — refinement proceeds on whatever
    signal actually exists (TechnicalDesign.md §5.3).
    """
    signal_lines = []
    for item in feedback:
        if item.rating is not None:
            detail = f" detail={item.rating_detail}" if item.rating_detail else ""
            signal_lines.append(f'- Rated {item.rating}/5{detail} — hook: "{item.hook}"')
        if item.final_text:
            generated_text = f"{item.hook} {item.body} {item.cta}"
            if item.final_text.strip() != generated_text.strip():
                diff = _summarize_edit_diff(generated_text, item.final_text)
                signal_lines.append(f'- Creator edited before use: {diff}')

    if len(outcomes) >= _MIN_OUTCOMES_FOR_PERFORMANCE_SIGNAL:
        # outcomes is already ordered best-performing first.
        top = outcomes[:_OUTCOME_SIGNALS_PER_SIDE]
        bottom = outcomes[-_OUTCOME_SIGNALS_PER_SIDE:]
        for signal in top:
            signal_lines.append(
                f'- Published script got {signal.views} views (top performer) — '
                f'hook: "{signal.hook}"'
            )
        for signal in bottom:
            if signal not in top:
                signal_lines.append(
                    f'- Published script got {signal.views} views (bottom performer) — '
                    f'hook: "{signal.hook}"'
                )

    return signal_lines


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
        self,
        channel_id: UUID,
        base_instructions: str,
        template_version: int | None,
        high_confidence: bool,
    ) -> tuple[list[VoiceDNA], list[TranscriptSegment]]:
        transcript_groups = await self._repository.list_segments_by_transcript_for_channel(
            channel_id
        )
        batches = [
            transcript_groups[i : i + _BATCH_SIZE_TRANSCRIPTS]
            for i in range(0, len(transcript_groups), _BATCH_SIZE_TRANSCRIPTS)
        ]

        instructions = base_instructions
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
            await prompts.log_invocation(
                self._db,
                feature=_EXTRACTION_PROMPT_FEATURE,
                template_version=template_version,
                rendered_prompt=prompt,
                reference_id=channel_id,
            )
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

        base_instructions, template_version = await prompts.get_active_prompt(
            self._db, self._redis, _EXTRACTION_PROMPT_FEATURE, _DEFAULT_EXTRACTION_INSTRUCTIONS
        )
        high_confidence = transcript_count >= _ANTI_FABRICATION_THRESHOLD
        batch_results, all_segments = await self._run_batches(
            channel_id, base_instructions, template_version, high_confidence
        )
        if not batch_results:
            raise RuntimeError(
                f"voice_dna agent returned unparseable output for every batch, channel {channel_id}"
            )

        voice_dna, phrases_confirmed = _consolidate(batch_results)
        curated = _curate_excerpts(all_segments)
        confidence = _compute_confidence(transcript_count, phrases_confirmed)
        extraction_prompt_version = prompts.format_prompt_version(template_version, PROMPT_VERSION)

        next_version = await self._repository.get_latest_version_number(channel_id) + 1
        profile = await self._repository.create_version(
            channel_id=channel_id,
            version=next_version,
            profile=voice_dna.model_dump(),
            confidence=confidence,
            excerpt_ids=[str(s.id) for s in curated],
            extraction_prompt_version=extraction_prompt_version,
            source="initial",
        )
        await channels.set_current_voice_profile_id(self._db, channel_id, profile.id)
        await self._refresh_prompt_block_cache(channel_id, voice_dna, curated, confidence)
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
        outcomes = await scripts.list_outcome_signals_since(
            self._db, channel_id, latest.created_at
        )
        signal_lines = _build_feedback_signal_lines(feedback, outcomes)
        if not signal_lines:
            return False

        refinement_instructions, template_version = await prompts.get_active_prompt(
            self._db, self._redis, _REFINEMENT_PROMPT_FEATURE, _DEFAULT_REFINEMENT_INSTRUCTIONS
        )
        current_profile = VoiceDNA.model_validate(latest.profile)
        prompt = (
            "Current Voice DNA profile (JSON):\n"
            f"{current_profile.model_dump_json()}\n\n"
            "New creator feedback signals since this profile was set:\n"
            + "\n".join(signal_lines)
            + "\n\n"
            + refinement_instructions
        )

        agent = get_voice_dna_agent()
        result = await agent.arun(prompt)
        await prompts.log_invocation(
            self._db,
            feature=_REFINEMENT_PROMPT_FEATURE,
            template_version=template_version,
            rendered_prompt=prompt,
            reference_id=channel_id,
        )
        if not isinstance(result.content, VoiceDNA):
            raise RuntimeError(
                f"voice_dna agent returned unparseable refinement output for channel {channel_id}"
            )
        voice_dna: VoiceDNA = result.content

        # Refinement makes one uncorroborated LLM call — confidence can only
        # be carried forward or downgraded, never upgraded, by this pass.
        # signature_phrases is the dimension most at risk of a plausible-
        # looking fabrication, so it's the one explicitly re-checked here.
        new_confidence = dict(latest.confidence)
        new_confidence["signature_phrases"] = _refine_signature_phrase_confidence(
            latest.confidence.get("signature_phrases", "low"),
            current_profile.signature_phrases,
            voice_dna.signature_phrases,
        )

        next_version = latest.version + 1
        refinement_prompt_version = prompts.format_prompt_version(template_version, PROMPT_VERSION)
        profile = await self._repository.create_version(
            channel_id=channel_id,
            version=next_version,
            profile=voice_dna.model_dump(),
            confidence=new_confidence,
            excerpt_ids=latest.excerpt_ids,
            extraction_prompt_version=refinement_prompt_version,
            source="refinement",
        )
        await channels.set_current_voice_profile_id(self._db, channel_id, profile.id)
        excerpt_segments = await self._repository.list_segments_by_ids(
            [UUID(i) for i in latest.excerpt_ids]
        )
        await self._refresh_prompt_block_cache(
            channel_id, voice_dna, excerpt_segments, new_confidence
        )
        emit(
            VOICE_PROFILE_UPDATED,
            {"channel_id": str(channel_id), "version": next_version},
        )
        return True

    # --------------------------------------------------------------- prompt block

    async def _refresh_prompt_block_cache(
        self,
        channel_id: UUID,
        voice_dna: VoiceDNA,
        excerpts: list[TranscriptSegment],
        confidence: dict[str, str] | None = None,
    ) -> None:
        await self._redis.set(
            f"promptblock:{channel_id}",
            _format_prompt_block(voice_dna, excerpts, confidence),
            ex=_PROMPT_BLOCK_CACHE_TTL_SECONDS,
        )


_NOT_YET_AVAILABLE = "Voice DNA: not yet available"


def _format_prompt_block(
    voice_dna: VoiceDNA,
    excerpts: list[TranscriptSegment] | None = None,
    confidence: dict[str, str] | None = None,
) -> str:
    confidence = confidence or {}
    lines = ["Voice DNA:"]
    for field, value in voice_dna.model_dump().items():
        tier = confidence.get(field)
        if field == "signature_phrases" and tier == "low":
            # An unconfirmed catchphrase is worse to hand to the script
            # generator than none at all (TechnicalDesign.md §6.2) — omit
            # the whole field rather than pass along a guess.
            continue
        if isinstance(value, list):
            value = ", ".join(value) or "none"
        suffix = " (tentative)" if tier == "low" else ""
        lines.append(f"  {field}{suffix}: {value}")

    selected = _select_prompt_excerpts(excerpts or [])
    if selected:
        # Verbatim passages, not paraphrased descriptions — a creator's own
        # phrasing teaches an LLM "voice" better than adjectives describing
        # it (TechnicalDesign.md §6.1).
        lines.append("Example passages in this creator's own words:")
        lines.extend(_format_excerpt_lines(selected))
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

    repository = VoiceProfileRepository(db)
    voice_profile = await repository.get_by_id(profile_id)
    if voice_profile is None:
        return _NOT_YET_AVAILABLE

    try:
        voice_dna = VoiceDNA.model_validate(voice_profile.profile)
    except Exception as exc:
        log.warning(
            "voice_dna_shape_mismatch", channel_id=str(channel_id), error=str(exc)
        )
        return _NOT_YET_AVAILABLE

    excerpt_segments = await repository.list_segments_by_ids(
        [UUID(i) for i in voice_profile.excerpt_ids]
    )
    block = _format_prompt_block(voice_dna, excerpt_segments, voice_profile.confidence)
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
