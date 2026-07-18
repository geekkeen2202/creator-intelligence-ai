from datetime import date
from uuid import uuid4

from app.modules import billing as billing_module
from app.modules.billing.service import BillingService


class FakeBillingRepository:
    def __init__(self):
        self.summary: list = []

    async def usage_summary(self, user_id, since):
        return self.summary


async def test_get_usage_summary_passes_through():
    repo = FakeBillingRepository()
    repo.summary = [
        {"feature": "script", "total_tokens": 100, "total_cost": 0.5, "total_scripts_generated": 3},
        {
            "feature": "whisper_minutes",
            "total_tokens": 0,
            "total_cost": 1.2,
            "total_scripts_generated": 0,
        },
    ]
    service = BillingService(repo, payments=None)

    summary = await service.get_usage_summary(uuid4(), date(2026, 1, 1))

    assert summary == repo.summary


class FakeSubscription:
    def __init__(self, plan):
        self.plan = plan


class FakeBillingRepositoryWithSubscription:
    def __init__(self, subscription):
        self._subscription = subscription

    async def get_active_for_user(self, user_id):
        return self._subscription


async def test_get_active_plan_returns_none_when_no_subscription(monkeypatch):
    monkeypatch.setattr(
        billing_module,
        "BillingRepository",
        lambda db: FakeBillingRepositoryWithSubscription(None),
    )

    assert await billing_module.get_active_plan(db=None, user_id=uuid4()) is None


async def test_get_active_plan_returns_plan_for_active_subscription(monkeypatch):
    monkeypatch.setattr(
        billing_module,
        "BillingRepository",
        lambda db: FakeBillingRepositoryWithSubscription(FakeSubscription("creator")),
    )

    assert await billing_module.get_active_plan(db=None, user_id=uuid4()) == "creator"
