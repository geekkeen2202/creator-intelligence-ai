import json
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.prompts.models import PromptTemplate
from app.modules.prompts.repository import PromptRepository

_TEMPLATE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # safety-net TTL; real
# invalidation happens synchronously on set_active_template (same pattern
# as voice_profiles' prompt-block cache).


def _cache_key(feature: str) -> str:
    return f"prompttemplate:{feature}"


async def _cache_template(redis: Redis, feature: str, template: str, version: int) -> None:
    await redis.set(
        _cache_key(feature),
        json.dumps({"template": template, "version": version}),
        ex=_TEMPLATE_CACHE_TTL_SECONDS,
    )


async def get_active_prompt(
    db: AsyncSession, redis: Redis, feature: str, default: str
) -> tuple[str, int | None]:
    """Cache-first read of a feature's active prompt template. Falls back to
    the caller-supplied `default` (the prompt text that used to be
    hardcoded) with version=None if no DB row exists yet for this feature —
    a feature never blocks on prompt-template migration (graceful
    degradation, same principle as voice_profiles.get_prompt_block).
    """
    cache_key = _cache_key(feature)
    cached = await redis.get(cache_key)
    if cached is not None:
        data = json.loads(cached)
        return data["template"], data["version"]

    template = await PromptRepository(db).get_active_template(feature)
    if template is None:
        return default, None

    await _cache_template(redis, feature, template.template, template.version)
    return template.template, template.version


async def set_active_template(
    db: AsyncSession, redis: Redis, feature: str, template: str
) -> PromptTemplate:
    """Creates a new active version for `feature` — the write path for
    editing/rolling back a prompt without a code deploy. Refreshes the cache
    immediately so the very next call sees it."""
    row = await PromptRepository(db).create_version(feature=feature, template=template)
    await _cache_template(redis, feature, row.template, row.version)
    return row


async def log_invocation(
    db: AsyncSession,
    *,
    feature: str,
    template_version: int | None,
    rendered_prompt: str,
    reference_id: UUID | None = None,
) -> None:
    await PromptRepository(db).log_invocation(
        feature=feature,
        template_version=template_version,
        rendered_prompt=rendered_prompt,
        reference_id=reference_id,
    )


def format_prompt_version(template_version: int | None, fallback: str) -> str:
    """The provenance `prompt_version` string for a call: ties directly to
    the DB template version actually used (`v{n}`) when one exists, or the
    caller's static fallback (e.g. an AgentEntry.prompt_version) when the
    feature hasn't been migrated to a DB template yet."""
    return f"v{template_version}" if template_version is not None else fallback
