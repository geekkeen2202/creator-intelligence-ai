from types import SimpleNamespace
from uuid import uuid4

from app.modules.prompts import service as service_module
from app.modules.prompts.service import (
    format_prompt_version,
    get_active_prompt,
    log_invocation,
    set_active_template,
)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


class FakePromptRepository:
    def __init__(self, db):
        self.db = db
        self.log_calls: list[dict] = []
        self.active_template: SimpleNamespace | None = None
        self.created_kwargs: dict | None = None

    async def get_active_template(self, feature):
        return self.active_template

    async def create_version(self, *, feature, template):
        row = SimpleNamespace(
            id=uuid4(), feature=feature, version=2, template=template, is_active=True
        )
        self.created_kwargs = {"feature": feature, "template": template}
        return row

    async def log_invocation(self, *, feature, template_version, rendered_prompt, reference_id):
        self.log_calls.append(
            {
                "feature": feature,
                "template_version": template_version,
                "rendered_prompt": rendered_prompt,
                "reference_id": reference_id,
            }
        )


async def test_get_active_prompt_returns_default_when_no_db_row(monkeypatch):
    fake_repo = FakePromptRepository(db=None)
    monkeypatch.setattr(service_module, "PromptRepository", lambda db: fake_repo)

    text, version = await get_active_prompt(
        db=None, redis=FakeRedis(), feature="script_generation", default="fallback text"
    )

    assert text == "fallback text"
    assert version is None


async def test_get_active_prompt_returns_db_row_and_caches_it(monkeypatch):
    fake_repo = FakePromptRepository(db=None)
    fake_repo.active_template = SimpleNamespace(template="db text", version=3)
    monkeypatch.setattr(service_module, "PromptRepository", lambda db: fake_repo)

    redis = FakeRedis()
    text, version = await get_active_prompt(
        db=None, redis=redis, feature="script_generation", default="fallback text"
    )

    assert (text, version) == ("db text", 3)
    assert "prompttemplate:script_generation" in redis.store


async def test_get_active_prompt_is_cache_first(monkeypatch):
    # If the cache has an entry, the repository must not be consulted at all.
    def blow_up(db):
        raise AssertionError("should not query the DB on a cache hit")

    monkeypatch.setattr(service_module, "PromptRepository", blow_up)

    redis = FakeRedis()
    redis.store["prompttemplate:script_generation"] = '{"template": "cached text", "version": 7}'

    text, version = await get_active_prompt(
        db=None, redis=redis, feature="script_generation", default="fallback text"
    )

    assert (text, version) == ("cached text", 7)


async def test_set_active_template_writes_version_and_refreshes_cache(monkeypatch):
    fake_repo = FakePromptRepository(db=None)
    monkeypatch.setattr(service_module, "PromptRepository", lambda db: fake_repo)

    redis = FakeRedis()
    row = await set_active_template(
        db=None, redis=redis, feature="script_generation", template="new text"
    )

    assert row.version == 2
    assert fake_repo.created_kwargs == {"feature": "script_generation", "template": "new text"}
    assert (
        redis.store["prompttemplate:script_generation"]
        == '{"template": "new text", "version": 2}'
    )


async def test_log_invocation_delegates_to_repository(monkeypatch):
    fake_repo = FakePromptRepository(db=None)
    monkeypatch.setattr(service_module, "PromptRepository", lambda db: fake_repo)

    reference_id = uuid4()
    await log_invocation(
        db=None,
        feature="script_generation",
        template_version=2,
        rendered_prompt="the full assembled prompt",
        reference_id=reference_id,
    )

    assert fake_repo.log_calls == [
        {
            "feature": "script_generation",
            "template_version": 2,
            "rendered_prompt": "the full assembled prompt",
            "reference_id": reference_id,
        }
    ]


def test_format_prompt_version_uses_db_version_when_present():
    assert format_prompt_version(5, "v1") == "v5"


def test_format_prompt_version_falls_back_when_no_db_version():
    assert format_prompt_version(None, "v1") == "v1"
