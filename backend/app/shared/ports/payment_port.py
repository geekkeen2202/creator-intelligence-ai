from typing import Any, Protocol


class Subscription(Protocol):
    id: str
    status: str


class PaymentPort(Protocol):
    async def create_subscription(self, user_id: str, plan: str) -> Any: ...

    async def cancel(self, subscription_id: str) -> None: ...

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool: ...
