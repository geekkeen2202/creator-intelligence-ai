import hashlib
import hmac
from typing import Any

import httpx

from app.config import get_settings

_BASE_URL = "https://api.razorpay.com/v1"

_PLAN_IDS: dict[str, str] = {
    # populated with Razorpay plan IDs once created in the dashboard
}


class RazorpayAdapter:
    """Implements PaymentPort via Razorpay. StripeAdapter added Year 2 with zero logic changes."""

    def __init__(self) -> None:
        settings = get_settings()
        self._key_id = settings.razorpay_key_id
        self._key_secret = settings.razorpay_key_secret
        self._webhook_secret = settings.razorpay_webhook_secret

    async def create_subscription(self, user_id: str, plan: str) -> dict[str, Any]:
        async with httpx.AsyncClient(auth=(self._key_id, self._key_secret)) as client:
            response = await client.post(
                f"{_BASE_URL}/subscriptions",
                json={
                    "plan_id": _PLAN_IDS[plan],
                    "customer_notify": 1,
                    "notes": {"user_id": user_id},
                },
            )
            response.raise_for_status()
            return response.json()

    async def cancel(self, subscription_id: str) -> None:
        async with httpx.AsyncClient(auth=(self._key_id, self._key_secret)) as client:
            response = await client.post(f"{_BASE_URL}/subscriptions/{subscription_id}/cancel")
            response.raise_for_status()

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        if not self._webhook_secret:
            # An empty secret must never validate — otherwise HMAC of an empty
            # key trivially "matches" a signature computed the same way.
            return False
        expected = hmac.new(self._webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
