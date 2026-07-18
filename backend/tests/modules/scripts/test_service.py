from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.scripts import router as scripts_router_module
from app.modules.scripts import service as service_module
from app.modules.scripts.router import get_rating_measurement
from app.modules.scripts.service import (
    ScriptGenerationLimitError,
    ScriptNotFoundError,
    ScriptService,
)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def incr(self, key):
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    async def expire(self, key, seconds):
        pass

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


class FakeScriptRepository:
    def __init__(self):
        self.scripts: dict = {}
        self.created_kwargs: dict | None = None
        self.rating_summary: list = []

    async def get_by_id(self, script_id):
        return self.scripts.get(script_id)

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        script = SimpleNamespace(id=uuid4(), **kwargs)
        self.scripts[script.id] = script
        return script

    async def set_rating(self, script_id, rating, detail=None):
        script = self.scripts.get(script_id)
        if script is not None:
            script.rating = rating
            script.rating_detail = detail
        return script

    async def rating_summary_by_profile_version(self, channel_id):
        return self.rating_summary


class FakeRunOutput:
    def __init__(self, content, input_tokens=10, output_tokens=20, cost=0.001):
        self.content = content
        self.metrics = SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens, cost=cost
        )


class FakeAgent:
    def __init__(self, content):
        self._content = content

    async def arun(self, prompt):
        return FakeRunOutput(self._content)


@pytest.fixture
def repo():
    return FakeScriptRepository()


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture(autouse=True)
def fake_active_plan(monkeypatch):
    async def fake_get_active_plan(db, user_id):
        return None  # free tier by default; override per-test if needed

    monkeypatch.setattr(service_module.billing, "get_active_plan", fake_get_active_plan)


async def test_generate_stamps_provenance_and_uses_voice_dna_prompt_block(
    repo, redis, monkeypatch
):
    generated = SimpleNamespace(
        hook="h", body="b", cta="c", b_roll_suggestions=[], power_word_spans=[],
        estimated_duration_seconds=None,
    )
    monkeypatch.setattr(
        service_module, "get_script_agent", lambda platform="youtube_long": FakeAgent(generated)
    )

    async def fake_prompt_block(db, redis_, channel_id):
        return "Voice DNA:\n  tone: energetic"

    async def fake_channel_context(db, redis_, channel_id):
        return SimpleNamespace(topics=[], videos=[])

    async def fake_profile_version(db, channel_id):
        return 3

    monkeypatch.setattr(
        service_module.voice_profiles, "get_prompt_block", fake_prompt_block
    )
    monkeypatch.setattr(
        service_module.voice_profiles, "get_current_profile_version", fake_profile_version
    )
    monkeypatch.setattr(service_module.trending, "get_channel_context", fake_channel_context)

    billed = []

    async def fake_increment(db, user_id, day):
        billed.append("increment")

    async def fake_record_usage(db, user_id, day, **kwargs):
        billed.append(kwargs)

    monkeypatch.setattr(service_module.billing, "increment_script_usage", fake_increment)
    monkeypatch.setattr(service_module.billing, "record_usage", fake_record_usage)

    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )

    service = ScriptService(repo, db=None, redis=redis)
    user_id, channel_id = uuid4(), uuid4()

    script = await service.generate(
        user_id=user_id, channel_id=channel_id, topic="topic", premium=False
    )

    assert script.hook == "h"
    assert repo.created_kwargs["voice_profile_version"] == 3
    assert repo.created_kwargs["agent_name"] == "script"
    assert repo.created_kwargs["agent_version"] == "v1"
    assert repo.created_kwargs["prompt_version"] == "v1"
    assert repo.created_kwargs["input_tokens"] == 10
    assert repo.created_kwargs["output_tokens"] == 20
    assert repo.created_kwargs["cost"] == 0.001
    assert billed[1] == {"feature": "script", "tokens": 30, "cost": 0.001}
    assert events == [("script.generated", {"user_id": str(user_id), "script_id": str(script.id)})]


async def test_generate_falls_back_when_no_voice_profile_yet(repo, redis, monkeypatch):
    generated = SimpleNamespace(
        hook="h", body="b", cta="c", b_roll_suggestions=[], power_word_spans=[],
        estimated_duration_seconds=None,
    )
    monkeypatch.setattr(
        service_module, "get_script_agent", lambda platform="youtube_long": FakeAgent(generated)
    )

    async def fake_prompt_block(db, redis_, channel_id):
        return "Voice DNA: not yet available"

    async def fake_channel_context(db, redis_, channel_id):
        return SimpleNamespace(topics=[], videos=[])

    async def fake_profile_version(db, channel_id):
        return None

    async def fake_noop(*a, **k):
        return None

    monkeypatch.setattr(service_module.voice_profiles, "get_prompt_block", fake_prompt_block)
    monkeypatch.setattr(
        service_module.voice_profiles, "get_current_profile_version", fake_profile_version
    )
    monkeypatch.setattr(service_module.trending, "get_channel_context", fake_channel_context)
    monkeypatch.setattr(service_module.billing, "increment_script_usage", fake_noop)
    monkeypatch.setattr(service_module.billing, "record_usage", fake_noop)
    monkeypatch.setattr(service_module, "emit", lambda *a, **k: None)

    service = ScriptService(repo, db=None, redis=redis)
    script = await service.generate(
        user_id=uuid4(), channel_id=uuid4(), topic="topic", premium=False
    )

    assert script.voice_profile_version is None


async def test_publish_denies_cross_user_script(repo, redis):
    owner_id = uuid4()
    other_user_id = uuid4()
    script_id = uuid4()
    repo.scripts[script_id] = SimpleNamespace(id=script_id, user_id=owner_id)

    service = ScriptService(repo, db=None, redis=redis)

    with pytest.raises(ScriptNotFoundError):
        await service.publish(
            user_id=other_user_id, script_id=script_id, external_video_id="abc"
        )


async def test_publish_emits_for_owner(repo, redis, monkeypatch):
    owner_id = uuid4()
    script_id = uuid4()
    repo.scripts[script_id] = SimpleNamespace(id=script_id, user_id=owner_id)

    events = []
    monkeypatch.setattr(
        service_module, "emit", lambda name, payload: events.append((name, payload))
    )

    service = ScriptService(repo, db=None, redis=redis)
    await service.publish(user_id=owner_id, script_id=script_id, external_video_id="vid123")

    assert events == [
        ("script.published", {"script_id": str(script_id), "external_video_id": "vid123"})
    ]


async def test_rate_denies_cross_user_script(repo, redis):
    owner_id = uuid4()
    script_id = uuid4()
    repo.scripts[script_id] = SimpleNamespace(id=script_id, user_id=owner_id)

    service = ScriptService(repo, db=None, redis=redis)

    with pytest.raises(ScriptNotFoundError):
        await service.rate(user_id=uuid4(), script_id=script_id, rating=5)


async def test_rate_limit_uses_free_tier_default_when_no_plan(redis, monkeypatch):
    async def fake_get_active_plan(db, user_id):
        return None

    monkeypatch.setattr(service_module.billing, "get_active_plan", fake_get_active_plan)
    service = ScriptService(None, db=None, redis=redis)
    user_id = uuid4()

    for _ in range(service_module._RATE_LIMIT_BY_PLAN["free"]):
        await service._check_rate_limit(user_id)

    with pytest.raises(ScriptGenerationLimitError):
        await service._check_rate_limit(user_id)


async def test_rate_limit_uses_higher_ceiling_for_paid_plan(redis, monkeypatch):
    async def fake_get_active_plan(db, user_id):
        return "creator"

    monkeypatch.setattr(service_module.billing, "get_active_plan", fake_get_active_plan)
    service = ScriptService(None, db=None, redis=redis)
    user_id = uuid4()

    # More than the free-tier limit succeeds because the plan is "creator".
    for _ in range(service_module._RATE_LIMIT_BY_PLAN["free"] + 1):
        await service._check_rate_limit(user_id)


async def test_get_rating_summary_by_profile_version_passes_through(repo, redis):
    repo.rating_summary = [
        {"voice_profile_version": 1, "rated_count": 3, "avg_rating": 4.0},
        {"voice_profile_version": 2, "rated_count": 2, "avg_rating": 4.5},
    ]
    service = ScriptService(repo, db=None, redis=redis)

    summary = await service.get_rating_summary_by_profile_version(uuid4())

    assert summary == repo.rating_summary


async def test_get_rating_measurement_denies_cross_user_access(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return False

    monkeypatch.setattr(scripts_router_module.channels, "verify_ownership", fake_verify_ownership)
    user = SimpleNamespace(user_id=str(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await get_rating_measurement(uuid4(), user, db=None, service=None)

    assert exc_info.value.status_code == 404
