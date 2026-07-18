from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.channels.router import get_channel


class FakeChannelService:
    def __init__(self, channel):
        self._channel = channel

    async def get_channel(self, channel_id):
        return self._channel


async def test_get_channel_denies_cross_user_access():
    owner_id = uuid4()
    other_user_id = uuid4()
    channel = SimpleNamespace(id=uuid4(), user_id=owner_id)
    user = SimpleNamespace(user_id=str(other_user_id))

    with pytest.raises(HTTPException) as exc_info:
        await get_channel(channel.id, user, FakeChannelService(channel))

    assert exc_info.value.status_code == 404


async def test_get_channel_returns_channel_for_owner():
    owner_id = uuid4()
    channel = SimpleNamespace(id=uuid4(), user_id=owner_id)
    user = SimpleNamespace(user_id=str(owner_id))

    result = await get_channel(channel.id, user, FakeChannelService(channel))

    assert result is channel


async def test_get_channel_denies_when_channel_missing():
    user = SimpleNamespace(user_id=str(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await get_channel(uuid4(), user, FakeChannelService(None))

    assert exc_info.value.status_code == 404
