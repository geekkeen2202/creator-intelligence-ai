from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scripts.repository import ScriptRepository
from app.modules.scripts.router import router as scripts_router
from app.modules.scripts.schemas import ScriptOwnedRead

__all__ = ["scripts_router", "get_script_for_owner"]


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
