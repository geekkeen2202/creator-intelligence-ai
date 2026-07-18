from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.analytics import router as router_module
from app.modules.analytics.router import get_channel_analytics


class FakeAnalyticsService:
    async def get_channel_analytics(self, channel_id, since):
        return [SimpleNamespace(channel_id=channel_id)]


async def test_get_channel_analytics_denies_cross_user_access(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return False

    monkeypatch.setattr(router_module.channels, "verify_ownership", fake_verify_ownership)
    user = SimpleNamespace(user_id=str(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await get_channel_analytics(uuid4(), user, db=None, service=FakeAnalyticsService())

    assert exc_info.value.status_code == 404


async def test_get_channel_analytics_returns_data_for_owner(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return True

    monkeypatch.setattr(router_module.channels, "verify_ownership", fake_verify_ownership)
    user = SimpleNamespace(user_id=str(uuid4()))
    channel_id = uuid4()

    result = await get_channel_analytics(
        channel_id, user, db=None, service=FakeAnalyticsService()
    )

    assert result[0].channel_id == channel_id
