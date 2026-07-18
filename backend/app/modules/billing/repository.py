from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import Subscription, Usage


class BillingRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_active_for_user(self, user_id: UUID) -> Subscription | None:
        result = await self._db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.status == "active"
            )
        )
        return result.scalars().first()

    async def create(
        self, *, user_id: UUID, plan: str, provider_subscription_id: str
    ) -> Subscription:
        sub = Subscription(
            user_id=user_id, plan=plan, provider_subscription_id=provider_subscription_id
        )
        self._db.add(sub)
        await self._db.commit()
        await self._db.refresh(sub)
        return sub

    async def set_status(self, provider_subscription_id: str, status: str) -> None:
        result = await self._db.execute(
            select(Subscription).where(
                Subscription.provider_subscription_id == provider_subscription_id
            )
        )
        sub = result.scalars().first()
        if sub is not None:
            sub.status = status
            await self._db.commit()

    async def increment_usage(
        self,
        user_id: UUID,
        day: date,
        *,
        feature: str = "script",
        tokens: int = 0,
        cost: float = 0,
    ) -> None:
        scripts_generated = 1 if feature == "script" else 0
        stmt = (
            insert(Usage)
            .values(
                user_id=user_id,
                day=day,
                feature=feature,
                scripts_generated=scripts_generated,
                tokens=tokens,
                cost=cost,
            )
            .on_conflict_do_update(
                index_elements=[Usage.user_id, Usage.day, Usage.feature],
                set_={
                    "scripts_generated": Usage.scripts_generated + scripts_generated,
                    "tokens": Usage.tokens + tokens,
                    "cost": Usage.cost + cost,
                },
            )
        )
        await self._db.execute(stmt)
        await self._db.commit()
