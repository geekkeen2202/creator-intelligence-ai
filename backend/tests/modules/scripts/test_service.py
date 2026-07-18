from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.scripts import service as service_module
from app.modules.scripts.service import (
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

    async def get_by_id(self, script_id):
        return self.scripts.get(script_id)

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        script = SimpleNamespace(id=uuid4(), **kwargs)
        self.scripts[script.id] = script
        return script

    async def set_rating(self, script_id, rating):
        script = self.scripts.get(script_id)
        if script is not None:
            script.rating = rating
        return script


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


async def test_generate_stamps_provenance_and_uses_voice_dna_prompt_block(
    repo, redis, monkeypatch
):
    generated = SimpleNamespace(hook="h", body="b", cta="c")
    monkeypatch.setattr(service_module, "get_script_agent", lambda: FakeAgent(generated))

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
    generated = SimpleNamespace(hook="h", body="b", cta="c")
    monkeypatch.setattr(service_module, "get_script_agent", lambda: FakeAgent(generated))

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
