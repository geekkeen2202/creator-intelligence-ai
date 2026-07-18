from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.trending import router as router_module
from app.modules.trending.router import get_trending_for_channel


class FakeTrendingService:
    async def get_channel_context(self, channel_id, language):
        return SimpleNamespace(channel_id=channel_id, language=language)


async def test_get_trending_for_channel_denies_cross_user_access(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return False

    monkeypatch.setattr(router_module.channels, "verify_ownership", fake_verify_ownership)
    user = SimpleNamespace(user_id=str(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await get_trending_for_channel(
            uuid4(), user, db=None, service=FakeTrendingService(), language="en"
        )

    assert exc_info.value.status_code == 404


async def test_get_trending_for_channel_returns_context_for_owner(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return True

    monkeypatch.setattr(router_module.channels, "verify_ownership", fake_verify_ownership)
    user = SimpleNamespace(user_id=str(uuid4()))
    channel_id = uuid4()

    result = await get_trending_for_channel(
        channel_id, user, db=None, service=FakeTrendingService(), language="en"
    )

    assert result.channel_id == channel_id
