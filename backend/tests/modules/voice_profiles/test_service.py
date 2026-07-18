from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.voice_profiles import service as service_module
from app.modules.voice_profiles.models import TranscriptSegment
from app.modules.voice_profiles.service import (
    VoiceProfileService,
    _format_prompt_block,
    _segment_text,
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

    async def create_transcript(self, *, video_id, source, quality_score):
        transcript = SimpleNamespace(id=uuid4(), video_id=video_id, source=source)
        self.transcripts.append(transcript)
        return transcript

    async def bulk_create_segments(self, segments):
        self.segments.extend(segments)

    async def count_transcripts_for_channel(self, channel_id):
        return self.transcript_count

    async def list_segments_for_channel(self, channel_id, limit=200):
        return self.segments

    async def get_latest_version_number(self, channel_id):
        return self.versions.get(channel_id, 0)

    async def create_version(self, **kwargs):
        self.created_version_kwargs = kwargs
        return SimpleNamespace(id=uuid4(), **kwargs)


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
        return "whisper text.", 0.6


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
    social = FakeSocial(captions=("Caption hook. Caption body. Caption cta.", 0.9))
    service = VoiceProfileService(
        repo, db=None, redis=None, social=social, transcription=FakeTranscription()
    )

    await service.transcribe_video(video_id)

    assert repo.transcripts[0].source == "captions"
    assert any(s.text == "Caption hook." for s in repo.segments)


async def test_transcribe_video_falls_back_to_whisper_when_no_captions():
    repo = FakeRepository()
    video_id = uuid4()
    repo.videos[video_id] = SimpleNamespace(
        id=video_id, channel_id=uuid4(), external_video_id="ext1"
    )
    social = FakeSocial(captions=None)
    service = VoiceProfileService(
        repo, db=None, redis=None, social=social, transcription=FakeTranscription()
    )

    await service.transcribe_video(video_id)

    assert repo.transcripts[0].source == "whisper"


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


async def test_extract_voice_profile_low_confidence_below_threshold(monkeypatch):
    repo = FakeRepository()
    repo.transcript_count = 2
    repo.segments = [
        TranscriptSegment(transcript_id=uuid4(), segment_type="hook", text="hi"),
    ]
    voice_dna = service_module.VoiceDNA(
        tone="casual",
        pacing="medium",
        vocabulary_level="simple",
        signature_phrases=[],
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

    assert repo.created_version_kwargs["confidence"]["tone"] == "low"
    assert repo.created_version_kwargs["version"] == 1
    assert len(set_calls) == 1
    assert set_calls[0][0] == channel_id  # repointed to the newly created version
    assert events == [("voice_profile.updated", {"channel_id": str(channel_id), "version": 1})]
    assert f"promptblock:{channel_id}" in redis.store  # cache refreshed immediately


def test_format_prompt_block_renders_list_fields():
    voice_dna = FakeVoiceDNA(
        {"tone": "energetic", "signature_phrases": ["let's go", "boom"]}
    )
    block = _format_prompt_block(voice_dna)
    assert "tone: energetic" in block
    assert "signature_phrases: let's go, boom" in block


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
