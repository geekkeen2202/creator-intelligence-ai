from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.repository import BillingRepository
from app.modules.billing.router import router as billing_router

__all__ = ["billing_router", "increment_script_usage", "record_usage", "get_active_plan"]


async def increment_script_usage(db: AsyncSession, user_id: UUID, day: date) -> None:
    """Public interface for other modules (e.g. scripts) recording daily usage."""
    await BillingRepository(db).increment_usage(user_id, day, feature="script")


async def record_usage(
    db: AsyncSession, user_id: UUID, day: date, *, feature: str, tokens: int = 0, cost: float = 0
) -> None:
    """Public interface for cost metering at source (ARCHITECTURE.md §8 rule
    11) — called at the moment LLM tokens or Whisper minutes are incurred.
    """
    await BillingRepository(db).increment_usage(
        user_id, day, feature=feature, tokens=tokens, cost=cost
    )


async def get_active_plan(db: AsyncSession, user_id: UUID) -> str | None:
    """Public interface for other modules (e.g. scripts' per-tier rate
    limiting, TechnicalDesign.md §5.2/M4.3) needing a user's active plan.
    None means no active subscription (free tier)."""
    subscription = await BillingRepository(db).get_active_for_user(user_id)
    return subscription.plan if subscription else None
