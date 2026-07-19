from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.ai.agents.voice_dna_agent import VoiceDNA
from app.modules.voice_profiles import service as service_module
from app.modules.voice_profiles.models import TranscriptSegment
from app.modules.voice_profiles.service import (
    VoiceProfileService,
    _build_feedback_signal_lines,
    _compute_confidence,
    _consolidate,
    _curate_excerpts,
    _format_prompt_block,
    _refine_signature_phrase_confidence,
    _segment_text,
    _select_prompt_excerpts,
    _summarize_edit_diff,
    get_prompt_block,
)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


class FakeRepository:
    def __init__(self):
        self.videos: dict = {}
        self.transcripts: list = []
        self.segments: list = []
        self.versions: dict = {}
        self.transcript_count = 0
        self.created_version_kwargs: dict | None = None

    async def get_video(self, video_id):
        return self.videos.get(video_id)

    async def create_transcript(
        self, *, video_id, source, quality_score, language_detected=None, raw_text="", clean_text=""
    ):
        transcript = SimpleNamespace(id=uuid4(), video_id=video_id, source=source)
        self.transcripts.append(transcript)
        return transcript

    async def bulk_create_segments(self, segments):
        self.segments.extend(segments)

    async def count_transcripts_for_channel(self, channel_id):
        return self.transcript_count

    async def list_segments_for_channel(self, channel_id, limit=200):
        return self.segments

    async def list_segments_by_transcript_for_channel(self, channel_id):
        return [self.segments] if self.segments else []

    async def get_latest_version_number(self, channel_id):
        return self.versions.get(channel_id, 0)

    async def get_latest_version(self, channel_id):
        return None

    async def create_version(self, **kwargs):
        self.created_version_kwargs = kwargs
        return SimpleNamespace(id=uuid4(), **kwargs)

    async def list_segments_by_ids(self, segment_ids):
        wanted = {str(i) for i in segment_ids}
        return [s for s in self.segments if str(s.id) in wanted]


class FakeSocial:
    def __init__(self, captions=None, audio=b"fake-audio"):
        self._captions = captions
        self._audio = audio

    async def get_captions(self, external_video_id):
        return self._captions

    async def download_audio(self, external_video_id):
        return self._audio


class FakeTranscription:
    async def transcribe(self, audio_bytes):
        return "whisper text.", 0.6, "english"


class FakeVoiceDNA:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


class FakeVoiceDNAResult:
    def __init__(self, content):
        self.content = content


class FakeAgent:
    def __init__(self, content):
        self._content = content

    async def arun(self, prompt):
        return FakeVoiceDNAResult(self._content)


def test_segment_text_splits_hook_body_cta():
    text = "This is the hook. This is body one. This is body two. This is the cta."
    segments = _segment_text(text)
    assert segments[0] == ("hook", "This is the hook.")
    assert segments[-1][0] == "cta"
    assert any(seg_type == "body" for seg_type, _ in segments)


def test_segment_text_single_sentence_is_body_only():
    assert _segment_text("Just one sentence.") == [("body", "Just one sentence.")]


def test_segment_text_empty_returns_nothing():
    assert _segment_text("") == []


async def test_transcribe_video_prefers_captions_over_whisper():
    repo = FakeRepository()
    video_id = uuid4()
    repo.videos[video_id] = SimpleNamespace(
        id=video_id, channel_id=uuid4(), external_video_id="ext1"
    )
    social = FakeSocial(captions=("Caption hook. Caption body. Caption cta.", 0.9, "en"))
    service = VoiceProfileService(
        repo, db=None, redis=None, social=social, transcription=FakeTranscription()
    )

    await service.transcribe_video(video_id)

    assert repo.transcripts[0].source == "captions"
    assert any(s.text == "Caption hook." for s in repo.segments)


async def test_transcribe_video_falls_back_to_whisper_when_no_captions(monkeypatch):
    repo = FakeRepository()
    video_id = uuid4()
    repo.videos[video_id] = SimpleNamespace(
        id=video_id, channel_id=uuid4(), external_video_id="ext1"
    )
    social = FakeSocial(captions=None)
    service = VoiceProfileService(
        repo, db=None, redis=None, social=social, transcription=FakeTranscription()
    )

    async def fake_get_owner(db, channel_id):
        return None  # exercises the "channel not found, skip metering" path

    monkeypatch.setattr(service_module.channels, "get_owner_user_id", fake_get_owner)

    await service.transcribe_video(video_id)

    assert repo.transcripts[0].source == "whisper"


async def test_transcribe_video_meters_whisper_cost(monkeypatch):
    repo = FakeRepository()
    video_id = uuid4()
    channel_id = uuid4()
    user_id = uuid4()
    repo.videos[video_id] = SimpleNamespace(
        id=video_id, channel_id=channel_id, external_video_id="ext1"
    )
    social = FakeSocial(captions=None)
    service = VoiceProfileService(
        repo, db=None, redis=None, social=social, transcription=FakeTranscription()
    )

    async def fake_get_owner(db, cid):
        assert cid == channel_id
        return user_id

    recorded = {}

    async def fake_record_usage(db, uid, day, **kwargs):
        recorded["user_id"] = uid
        recorded.update(kwargs)

    monkeypatch.setattr(service_module.channels, "get_owner_user_id", fake_get_owner)
    monkeypatch.setattr(service_module.billing, "record_usage", fake_record_usage)

    await service.transcribe_video(video_id)

    assert recorded["user_id"] == user_id
    assert recorded["feature"] == "whisper_minutes"
    assert recorded["cost"] > 0


async def test_transcribe_video_raises_when_no_captions_and_no_transcription_port():
    repo = FakeRepository()
    video_id = uuid4()
    repo.videos[video_id] = SimpleNamespace(
        id=video_id, channel_id=uuid4(), external_video_id="ext1"
    )
    social = FakeSocial(captions=None)
    service = VoiceProfileService(repo, db=None, redis=None, social=social, transcription=None)

    with pytest.raises(RuntimeError):
        await service.transcribe_video(video_id)


async def test_extract_voice_profile_skipped_below_minimum_transcripts():
    repo = FakeRepository()
    repo.transcript_count = 1
    service = VoiceProfileService(repo, db=None, redis=FakeRedis(), social=FakeSocial())

    await service.extract_voice_profile(uuid4())

    assert repo.created_version_kwargs is None


async def test_extract_voice_profile_raises_clearly_on_unparseable_llm_output(monkeypatch):
    # Free-tier LLM routing occasionally returns malformed/truncated JSON —
    # agno then leaves result.content as a raw string instead of a VoiceDNA.
    # This must fail loudly (so Celery's retry/logs are meaningful) rather
    # than crash deeper with a confusing AttributeError.
    repo = FakeRepository()
    repo.transcript_count = 2
    repo.segments = [
        TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text="hi"),
    ]
    monkeypatch.setattr(
        service_module, "get_voice_dna_agent", lambda: FakeAgent("not valid json")
    )

    service = VoiceProfileService(repo, db=None, redis=FakeRedis(), social=FakeSocial())

    with pytest.raises(RuntimeError, match="unparseable"):
        await service.extract_voice_profile(uuid4())

    assert repo.created_version_kwargs is None


async def test_extract_voice_profile_confidence_is_per_dimension(monkeypatch):
    # Below the minimum extraction threshold (2 transcripts, one batch, no
    # cross-batch recurrence check possible): dimensions that read off 1-2
    # videos are already "high", dimensions needing 3-5 are still "low", and
    # signature_phrases can never be above "low" without recurrence
    # (TechnicalDesign.md §6.2).
    repo = FakeRepository()
    repo.transcript_count = 2
    repo.segments = [
        TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text="hi"),
    ]
    voice_dna = service_module.VoiceDNA(
        tone="casual",
        pacing="medium",
        vocabulary_level="simple",
        signature_phrases=["let's go"],
        hook_style="question",
        cta_style="soft",
    )
    monkeypatch.setattr(service_module, "get_voice_dna_agent", lambda: FakeAgent(voice_dna))

    set_calls = []

    async def fake_set_current(db, channel_id, voice_profile_id):
        set_calls.append((channel_id, voice_profile_id))

    monkeypatch.setattr(service_module.channels, "set_current_voice_profile_id", fake_set_current)

    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )

    channel_id = uuid4()
    redis = FakeRedis()
    service = VoiceProfileService(repo, db=None, redis=redis, social=FakeSocial())
    await service.extract_voice_profile(channel_id)

    confidence = repo.created_version_kwargs["confidence"]
    assert confidence["tone"] == "high"
    assert confidence["vocabulary_level"] == "high"
    assert confidence["pacing"] == "medium"
    assert confidence["hook_style"] == "low"
    assert confidence["cta_style"] == "low"
    assert confidence["signature_phrases"] == "low"  # single batch, never cross-checked
    assert repo.created_version_kwargs["version"] == 1
    assert len(set_calls) == 1
    assert set_calls[0][0] == channel_id  # repointed to the newly created version
    assert events == [("voice_profile.updated", {"channel_id": str(channel_id), "version": 1})]
    assert f"promptblock:{channel_id}" in redis.store  # cache refreshed immediately
    # tentative fields marked; unconfirmed signature_phrases omitted entirely
    block = redis.store[f"promptblock:{channel_id}"]
    assert "hook_style (tentative)" in block
    assert "signature_phrases" not in block


def test_format_prompt_block_renders_list_fields():
    voice_dna = FakeVoiceDNA(
        {"tone": "energetic", "signature_phrases": ["let's go", "boom"]}
    )
    block = _format_prompt_block(voice_dna)
    assert "tone: energetic" in block
    assert "signature_phrases: let's go, boom" in block


def test_format_prompt_block_omits_excerpt_section_when_none_given():
    voice_dna = FakeVoiceDNA({"tone": "energetic"})
    block = _format_prompt_block(voice_dna, excerpts=[])
    assert "Example passages" not in block


def test_format_prompt_block_includes_verbatim_excerpts():
    voice_dna = FakeVoiceDNA({"tone": "energetic"})
    excerpts = [
        TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text="Yo what's up team."),
        TranscriptSegment(transcript_id=uuid4(), segment_type="cta", text="Smash that subscribe."),
    ]
    block = _format_prompt_block(voice_dna, excerpts=excerpts)
    assert "Example passages in this creator's own words:" in block
    assert '[hook] "Yo what\'s up team."' in block
    assert '[cta] "Smash that subscribe."' in block


def test_select_prompt_excerpts_prioritizes_hook_and_cta_and_caps_count():
    segments = (
        [TranscriptSegment(transcript_id=uuid4(), segment_type="body", text=f"body{n}") for n in range(6)]
        + [TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text="hook1")]
        + [TranscriptSegment(transcript_id=uuid4(), segment_type="cta", text="cta1")]
    )
    selected = _select_prompt_excerpts(segments)
    assert len(selected) <= 5
    assert selected[0].segment_type == "hook"
    assert selected[1].segment_type == "cta"


def test_format_prompt_block_truncates_long_excerpts():
    voice_dna = FakeVoiceDNA({"tone": "energetic"})
    long_text = "x" * 500
    excerpts = [TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text=long_text)]
    block = _format_prompt_block(voice_dna, excerpts=excerpts)
    assert "x" * 500 not in block
    assert "..." in block


async def test_get_prompt_block_returns_cached_value():
    redis = FakeRedis()
    channel_id = uuid4()
    redis.store[f"promptblock:{channel_id}"] = "Voice DNA:\n  tone: cached"

    result = await get_prompt_block(db=None, redis=redis, channel_id=channel_id)

    assert result == "Voice DNA:\n  tone: cached"


async def test_get_prompt_block_falls_back_when_no_profile(monkeypatch):
    async def fake_get_pointer(db, channel_id):
        return None

    monkeypatch.setattr(service_module.channels, "get_current_voice_profile_id", fake_get_pointer)

    result = await get_prompt_block(db=None, redis=FakeRedis(), channel_id=uuid4())

    assert result == "Voice DNA: not yet available"


async def test_get_prompt_block_falls_back_on_malformed_profile(monkeypatch):
    profile_id = uuid4()

    async def fake_get_pointer(db, channel_id):
        return profile_id

    class FakeVoiceProfileRepo:
        def __init__(self, db):
            pass

        async def get_by_id(self, pid):
            return SimpleNamespace(profile={"unexpected": "shape"})

    monkeypatch.setattr(service_module.channels, "get_current_voice_profile_id", fake_get_pointer)
    monkeypatch.setattr(service_module, "VoiceProfileRepository", FakeVoiceProfileRepo)

    result = await get_prompt_block(db=None, redis=FakeRedis(), channel_id=uuid4())

    assert result == "Voice DNA: not yet available"


async def test_get_prompt_block_fetches_and_includes_excerpts_on_cache_miss(monkeypatch):
    profile_id = uuid4()
    excerpt_segment = TranscriptSegment(
        id=uuid4(), transcript_id=uuid4(), segment_type="hook", text="Real creator words."
    )

    async def fake_get_pointer(db, channel_id):
        return profile_id

    class FakeVoiceProfileRepo:
        def __init__(self, db):
            pass

        async def get_by_id(self, pid):
            return SimpleNamespace(
                profile=_voice_dna(["let's go"]).model_dump(),
                excerpt_ids=[str(excerpt_segment.id)],
                confidence={"tone": "high", "signature_phrases": "high"},
            )

        async def list_segments_by_ids(self, segment_ids):
            wanted = {str(i) for i in segment_ids}
            return [excerpt_segment] if str(excerpt_segment.id) in wanted else []

    monkeypatch.setattr(service_module.channels, "get_current_voice_profile_id", fake_get_pointer)
    monkeypatch.setattr(service_module, "VoiceProfileRepository", FakeVoiceProfileRepo)

    redis = FakeRedis()
    result = await get_prompt_block(db=None, redis=redis, channel_id=uuid4())

    assert "Example passages in this creator's own words:" in result
    assert '[hook] "Real creator words."' in result
    assert redis.store  # cache populated on miss


def _voice_dna(phrases, tone="casual"):
    return VoiceDNA(
        tone=tone,
        pacing="medium",
        vocabulary_level="simple",
        signature_phrases=phrases,
        hook_style="question",
        cta_style="soft",
    )


def test_consolidate_single_batch_passthrough():
    only = _voice_dna(["let's go"])
    merged, phrases_confirmed = _consolidate([only])
    assert merged is only
    assert phrases_confirmed is False  # no cross-batch check was possible


def test_consolidate_keeps_only_recurring_phrases_across_batches():
    latest = _voice_dna(["let's go", "one-off phrase"])
    older = _voice_dna(["let's go", "different one-off"])
    merged, phrases_confirmed = _consolidate([latest, older])

    assert merged.tone == "casual"  # scalar fields come from latest (batches[0])
    assert "let's go" in merged.signature_phrases  # recurs across batches
    assert "one-off phrase" not in merged.signature_phrases  # only ever seen once
    assert "different one-off" not in merged.signature_phrases
    assert phrases_confirmed is True


def test_consolidate_keeps_all_phrases_when_nothing_recurs():
    latest = _voice_dna(["alpha"])
    older = _voice_dna(["beta"])
    merged, phrases_confirmed = _consolidate([latest, older])

    assert set(merged.signature_phrases) == {"alpha", "beta"}
    assert phrases_confirmed is False  # nothing actually recurred


def test_compute_confidence_varies_by_dimension_and_transcript_count():
    # 4 transcripts: tone/vocabulary_level already high (threshold 2), pacing
    # high (threshold 3), hook/cta_style medium (threshold 3-5), phrases low
    # (threshold 10, and unconfirmed here regardless).
    confidence = _compute_confidence(4, phrases_confirmed=False)
    assert confidence["tone"] == "high"
    assert confidence["vocabulary_level"] == "high"
    assert confidence["pacing"] == "high"
    assert confidence["hook_style"] == "medium"
    assert confidence["cta_style"] == "medium"
    assert confidence["signature_phrases"] == "low"


def test_compute_confidence_signature_phrases_needs_both_count_and_recurrence():
    # Enough transcripts (10) but phrases never actually recurred -> still low.
    assert _compute_confidence(10, phrases_confirmed=False)["signature_phrases"] == "low"
    # Enough transcripts and recurrence confirmed -> high.
    assert _compute_confidence(10, phrases_confirmed=True)["signature_phrases"] == "high"
    # Recurrence confirmed but corpus still thin -> not yet high.
    assert _compute_confidence(7, phrases_confirmed=True)["signature_phrases"] == "medium"


def test_refine_signature_phrase_confidence_keeps_prior_tier_when_subset():
    tier = _refine_signature_phrase_confidence("high", ["let's go", "boom"], ["let's go"])
    assert tier == "high"


def test_refine_signature_phrase_confidence_downgrades_on_new_phrase():
    tier = _refine_signature_phrase_confidence(
        "high", ["let's go"], ["let's go", "brand new phrase"]
    )
    assert tier == "low"


def test_summarize_edit_diff_shows_changed_words_only():
    diff = _summarize_edit_diff(
        "Hey guys welcome back to the channel", "Yo everyone welcome back to the channel"
    )
    assert '-"Hey guys"' in diff
    assert '+"Yo everyone"' in diff
    assert "welcome back to the channel" not in diff  # unchanged span omitted


def test_summarize_edit_diff_truncates_long_diffs():
    diff = _summarize_edit_diff("a " * 5, "b " * 500)
    assert diff.endswith("...")
    assert len(diff) <= service_module._MAX_EDIT_DIFF_CHARS + len("...")


def test_summarize_edit_diff_reports_whitespace_only_changes():
    assert _summarize_edit_diff("same text", "same  text") == "(only whitespace/formatting changed)"


def test_build_feedback_signal_lines_includes_rating_and_edit_diff():
    feedback = [
        SimpleNamespace(rating=4, rating_detail=None, hook="h1", body="b1", cta="c1", final_text=None),
        SimpleNamespace(
            rating=None,
            rating_detail=None,
            hook="Hey guys",
            body="middle stuff",
            cta="subscribe",
            final_text="Yo everyone middle stuff subscribe",
        ),
    ]
    lines = _build_feedback_signal_lines(feedback, outcomes=[])
    assert any(line.startswith("- Rated 4/5") for line in lines)
    assert any("Creator edited before use" in line for line in lines)


def test_build_feedback_signal_lines_ignores_edits_that_match_generated():
    feedback = [
        SimpleNamespace(
            rating=None, rating_detail=None, hook="h", body="b", cta="c", final_text="h b c"
        ),
    ]
    assert _build_feedback_signal_lines(feedback, outcomes=[]) == []


def test_build_feedback_signal_lines_omits_outcomes_below_minimum():
    from app.modules.scripts.schemas import ScriptOutcomeSignal

    outcomes = [ScriptOutcomeSignal(hook="h1", views=100)]
    assert _build_feedback_signal_lines([], outcomes) == []


def test_build_feedback_signal_lines_includes_top_and_bottom_performers():
    from app.modules.scripts.schemas import ScriptOutcomeSignal

    outcomes = [
        ScriptOutcomeSignal(hook="best", views=1000),
        ScriptOutcomeSignal(hook="second", views=800),
        ScriptOutcomeSignal(hook="third", views=50),
        ScriptOutcomeSignal(hook="worst", views=10),
    ]
    lines = _build_feedback_signal_lines([], outcomes)
    joined = "\n".join(lines)
    assert '"best"' in joined and "top performer" in joined
    assert '"worst"' in joined and "bottom performer" in joined


def test_curate_excerpts_prioritizes_hook_and_cta_variety():
    body_segments = [
        TranscriptSegment(transcript_id=uuid4(), segment_type="body", text="b" * n)
        for n in range(1, 10)
    ]
    segments = (
        body_segments
        + [TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text="hook text")]
        + [TranscriptSegment(transcript_id=uuid4(), segment_type="cta", text="cta text")]
    )
    curated = _curate_excerpts(segments)

    assert len(curated) <= 12
    assert any(s.segment_type == "hook" for s in curated)
    assert any(s.segment_type == "cta" for s in curated)


async def test_refine_profile_returns_false_when_no_prior_version():
    repo = FakeRepository()
    service = VoiceProfileService(repo, db=None, redis=FakeRedis(), social=FakeSocial())

    refined = await service.refine_profile(uuid4())

    assert refined is False


async def test_refine_profile_returns_false_when_no_new_signal(monkeypatch):
    repo = FakeRepository()
    latest = SimpleNamespace(
        version=1,
        created_at=uuid4(),  # unused by the fake below, just needs to exist
        profile=_voice_dna(["x"]).model_dump(),
        confidence={"tone": "high"},
        excerpt_ids=[],
    )
    repo.get_latest_version = lambda channel_id: _async_return(latest)

    async def fake_list_feedback_since(db, channel_id, since):
        return []

    async def fake_list_outcome_signals_since(db, channel_id, since):
        return []

    monkeypatch.setattr(service_module.scripts, "list_feedback_since", fake_list_feedback_since)
    monkeypatch.setattr(
        service_module.scripts, "list_outcome_signals_since", fake_list_outcome_signals_since
    )

    service = VoiceProfileService(repo, db=None, redis=FakeRedis(), social=FakeSocial())
    refined = await service.refine_profile(uuid4())

    assert refined is False


async def test_refine_profile_downgrades_signature_phrase_confidence_on_new_phrase(monkeypatch):
    repo = FakeRepository()
    latest = SimpleNamespace(
        version=1,
        created_at=uuid4(),
        profile=_voice_dna(["let's go"]).model_dump(),
        confidence={"tone": "high", "signature_phrases": "high"},
        excerpt_ids=[],
    )
    repo.get_latest_version = lambda channel_id: _async_return(latest)

    async def fake_list_feedback_since(db, channel_id, since):
        return [SimpleNamespace(rating=5, rating_detail=None, hook="h", body="b", cta="c", final_text=None)]

    async def fake_list_outcome_signals_since(db, channel_id, since):
        return []

    monkeypatch.setattr(service_module.scripts, "list_feedback_since", fake_list_feedback_since)
    monkeypatch.setattr(
        service_module.scripts, "list_outcome_signals_since", fake_list_outcome_signals_since
    )
    monkeypatch.setattr(service_module.channels, "set_current_voice_profile_id", lambda *a: _async_return(None))
    monkeypatch.setattr(service_module, "emit", lambda name, payload: None)

    # Refined output introduces a phrase never seen in the prior, corpus-
    # confirmed profile — confidence must downgrade, not carry forward.
    refined_dna = _voice_dna(["let's go", "a brand new guess"])
    monkeypatch.setattr(service_module, "get_voice_dna_agent", lambda: FakeAgent(refined_dna))

    service = VoiceProfileService(repo, db=None, redis=FakeRedis(), social=FakeSocial())
    refined = await service.refine_profile(uuid4())

    assert refined is True
    assert repo.created_version_kwargs["confidence"]["signature_phrases"] == "low"
    assert repo.created_version_kwargs["confidence"]["tone"] == "high"  # untouched dimension kept


async def _async_return(value):
    return value
