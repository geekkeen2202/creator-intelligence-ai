from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.prompts import service
from app.modules.prompts.models import PromptTemplate

__all__ = ["format_prompt_version", "get_active_prompt", "log_invocation", "set_active_template"]


async def get_active_prompt(
    db: AsyncSession, redis: Redis, feature: str, default: str
) -> tuple[str, int | None]:
    """Public interface for other modules — the active prompt template text
    for `feature`, plus its version (None if no DB row exists yet, in which
    case `default` — the value that used to be hardcoded in the calling
    module — is returned instead). Cache-first, event-driven invalidation on
    writes (see prompts.service)."""
    return await service.get_active_prompt(db, redis, feature, default)


async def log_invocation(
    db: AsyncSession,
    *,
    feature: str,
    template_version: int | None,
    rendered_prompt: str,
    reference_id: UUID | None = None,
) -> None:
    """Public interface — records the fully assembled prompt actually sent
    to an LLM for a given call, independent of whether that call produced a
    persisted business row. Audit/debugging trail, not read at runtime."""
    await service.log_invocation(
        db,
        feature=feature,
        template_version=template_version,
        rendered_prompt=rendered_prompt,
        reference_id=reference_id,
    )


async def set_active_template(
    db: AsyncSession, redis: Redis, feature: str, template: str
) -> PromptTemplate:
    """Public interface — writes a new active template version for
    `feature` (the edit/rollback path). Old versions stay in history, never
    deleted; only one row per feature has is_active=True at a time."""
    return await service.set_active_template(db, redis, feature, template)


def format_prompt_version(template_version: int | None, fallback: str) -> str:
    """Public interface — the provenance `prompt_version` string for a call:
    `v{n}` when a DB template version was actually used, else `fallback`."""
    return service.format_prompt_version(template_version, fallback)
