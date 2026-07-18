from datetime import date
from uuid import UUID

from app.modules.billing.events import (
    SUBSCRIPTION_CANCELLED,
    SUBSCRIPTION_PAYMENT_FAILED,
    SUBSCRIPTION_STARTED,
)
from app.modules.billing.repository import BillingRepository
from app.shared.events import emit
from app.shared.ports.payment_port import PaymentPort


class BillingService:
    def __init__(self, repository: BillingRepository, payments: PaymentPort):
        self._repository = repository
        self._payments = payments

    async def start_subscription(self, user_id: UUID, plan: str):
        remote = await self._payments.create_subscription(str(user_id), plan)
        sub = await self._repository.create(
            user_id=user_id, plan=plan, provider_subscription_id=remote["id"]
        )
        emit(SUBSCRIPTION_STARTED, {"user_id": str(user_id), "subscription_id": str(sub.id)})
        return sub

    async def get_usage_summary(self, user_id: UUID, since: date) -> list[dict]:
        return await self._repository.usage_summary(user_id, since)

    async def handle_webhook_event(self, event_type: str, provider_subscription_id: str) -> None:
        if event_type == "subscription.cancelled":
            await self._repository.set_status(provider_subscription_id, "cancelled")
            emit(SUBSCRIPTION_CANCELLED, {"provider_subscription_id": provider_subscription_id})
        elif event_type == "subscription.payment_failed":
            await self._repository.set_status(provider_subscription_id, "payment_failed")
            emit(
                SUBSCRIPTION_PAYMENT_FAILED, {"provider_subscription_id": provider_subscription_id}
            )
