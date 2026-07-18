from types import SimpleNamespace
from uuid import uuid4

from app.modules import channels as channels_module


def _fake_repository_returning(channel):
    class FakeChannelRepository:
        def __init__(self, db):
            pass

        async def get_by_id(self, channel_id):
            return channel

    return FakeChannelRepository


async def test_verify_ownership_true_for_owner(monkeypatch):
    owner_id = uuid4()
    monkeypatch.setattr(
        channels_module,
        "ChannelRepository",
        _fake_repository_returning(SimpleNamespace(user_id=owner_id)),
    )

    assert await channels_module.verify_ownership(db=None, channel_id=uuid4(), user_id=owner_id)


async def test_verify_ownership_false_for_other_user(monkeypatch):
    owner_id = uuid4()
    monkeypatch.setattr(
        channels_module,
        "ChannelRepository",
        _fake_repository_returning(SimpleNamespace(user_id=owner_id)),
    )

    assert not await channels_module.verify_ownership(
        db=None, channel_id=uuid4(), user_id=uuid4()
    )


async def test_verify_ownership_false_when_channel_missing(monkeypatch):
    monkeypatch.setattr(channels_module, "ChannelRepository", _fake_repository_returning(None))

    assert not await channels_module.verify_ownership(db=None, channel_id=uuid4(), user_id=uuid4())
