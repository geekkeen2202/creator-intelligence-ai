from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scripts.repository import ScriptRepository
from app.modules.scripts.router import router as scripts_router
from app.modules.scripts.schemas import ScriptFeedbackRead, ScriptOutcomeSignal, ScriptOwnedRead

__all__ = [
    "scripts_router",
    "get_script_for_owner",
    "list_feedback_since",
    "list_outcome_signals_since",
    "list_unmatched_for_channel",
]


async def get_script_for_owner(
    db: AsyncSession, script_id: UUID, user_id: UUID
) -> ScriptOwnedRead | None:
    """Public interface for other modules (e.g. thumbnails) needing a
    script's content — ownership-checked here so callers never need their
    own copy of that check (ARCHITECTURE.md §4 rule 7)."""
    script = await ScriptRepository(db).get_by_id(script_id)
    if script is None or script.user_id != user_id:
        return None
    return ScriptOwnedRead.model_validate(script)


async def list_feedback_since(
    db: AsyncSession, channel_id: UUID, since: datetime
) -> list[ScriptFeedbackRead]:
    """Public interface for voice_profiles' weekly refinement job (§5.3) —
    rating/rating_detail/final_text signals since the last profile version,
    without voice_profiles touching scripts' repository directly."""
    scripts = await ScriptRepository(db).list_feedback_since(channel_id, since)
    return [ScriptFeedbackRead.model_validate(s) for s in scripts]


async def list_outcome_signals_since(
    db: AsyncSession, channel_id: UUID, since: datetime
) -> list[ScriptOutcomeSignal]:
    """Public interface for voice_profiles' weekly refinement job (§5.3) —
    published scripts' view counts since the last profile version, ordered
    best-performing first."""
    rows = await ScriptRepository(db).list_outcome_signals_since(channel_id, since)
    return [ScriptOutcomeSignal(hook=hook, views=views) for hook, views in rows]


async def list_unmatched_for_channel(db: AsyncSession, channel_id: UUID) -> list[ScriptOwnedRead]:
    """Public interface for the auto outcome-matching job (§5.5) — scripts on
    this channel with no linked outcome yet."""
    scripts = await ScriptRepository(db).list_unmatched_for_channel(channel_id)
    return [ScriptOwnedRead.model_validate(s) for s in scripts]
