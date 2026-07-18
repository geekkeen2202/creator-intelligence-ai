from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.thumbnails import service as service_module
from app.modules.thumbnails.service import ThumbnailScriptNotFoundError, ThumbnailService


class FakeThumbnailRepository:
    def __init__(self):
        self.created_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.created_kwargs = kwargs
        return SimpleNamespace(id=uuid4(), **kwargs)


class FakeRunOutput:
    def __init__(self, content):
        self.content = content
        self.metrics = SimpleNamespace(input_tokens=5, output_tokens=15, cost=0.0005)


class FakeAgent:
    def __init__(self, content):
        self._content = content

    async def arun(self, prompt):
        return FakeRunOutput(self._content)


@pytest.fixture
def repo():
    return FakeThumbnailRepository()


async def test_generate_denies_script_owned_by_another_user(repo, monkeypatch):
    async def fake_get_script_for_owner(db, script_id, user_id):
        return None  # scripts.get_script_for_owner already encodes the denial

    monkeypatch.setattr(service_module.scripts, "get_script_for_owner", fake_get_script_for_owner)

    service = ThumbnailService(repo, db=None)
    with pytest.raises(ThumbnailScriptNotFoundError):
        await service.generate(user_id=uuid4(), script_id=uuid4())

    assert repo.created_kwargs is None  # never reached the agent/repository


async def test_generate_stamps_provenance_for_owned_script(repo, monkeypatch):
    brief = SimpleNamespace(
        model_dump=lambda: {"text_overlay": "WOW"},
    )
    monkeypatch.setattr(service_module, "get_thumbnail_brief_agent", lambda: FakeAgent(brief))

    async def fake_get_script_for_owner(db, script_id, user_id):
        return SimpleNamespace(id=script_id, channel_id=uuid4(), hook="a great hook")

    async def fake_record_usage(*a, **k):
        return None

    monkeypatch.setattr(service_module.scripts, "get_script_for_owner", fake_get_script_for_owner)
    monkeypatch.setattr(service_module.billing, "record_usage", fake_record_usage)
    monkeypatch.setattr(service_module, "emit", lambda *a, **k: None)

    service = ThumbnailService(repo, db=None)
    script_id = uuid4()
    result = await service.generate(user_id=uuid4(), script_id=script_id)

    assert repo.created_kwargs["script_id"] == script_id
    assert repo.created_kwargs["brief"] == {"text_overlay": "WOW"}
    assert repo.created_kwargs["agent_name"] == "thumbnail_brief"
    assert repo.created_kwargs["agent_version"] == "v1"
    assert repo.created_kwargs["input_tokens"] == 5
    assert result.brief == {"text_overlay": "WOW"}
