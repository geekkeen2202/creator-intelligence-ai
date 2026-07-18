from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.voice_profiles import router as router_module
from app.modules.voice_profiles.router import get_channel_voice_profile


async def test_get_channel_voice_profile_denies_cross_user_access(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return False

    monkeypatch.setattr(router_module.channels, "verify_ownership", fake_verify_ownership)
    user = SimpleNamespace(user_id=str(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await get_channel_voice_profile(uuid4(), user, db=None)

    assert exc_info.value.status_code == 404


async def test_get_channel_voice_profile_returns_profile_for_owner(monkeypatch):
    async def fake_verify_ownership(db, channel_id, user_id):
        return True

    profile = SimpleNamespace(id=uuid4())

    async def fake_get_current(db, channel_id):
        return profile

    monkeypatch.setattr(router_module.channels, "verify_ownership", fake_verify_ownership)
    monkeypatch.setattr(router_module.service, "get_current", fake_get_current)
    user = SimpleNamespace(user_id=str(uuid4()))

    result = await get_channel_voice_profile(uuid4(), user, db=None)

    assert result is profile
