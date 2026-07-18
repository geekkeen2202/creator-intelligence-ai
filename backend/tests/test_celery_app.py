import json

from app.tasks import celery_app as celery_app_module


class FakeRedisClient:
    def __init__(self, *a, **k):
        self.pushed: list[str] = []

    def lpush(self, key, value):
        self.pushed.append(value)

    def ltrim(self, key, start, end):
        pass


def test_record_dead_letter_persists_failure(monkeypatch):
    fake_client = FakeRedisClient()

    class FakeRedisModule:
        class Redis:
            @staticmethod
            def from_url(url, decode_responses=True):
                return fake_client

    monkeypatch.setitem(__import__("sys").modules, "redis", FakeRedisModule)

    celery_app_module._record_dead_letter(
        sender=type("Task", (), {"name": "app.tasks.example.do_thing"})(),
        task_id="abc-123",
        exception=RuntimeError("boom"),
        args=(1, 2),
        kwargs={"x": "y"},
    )

    assert len(fake_client.pushed) == 1
    entry = json.loads(fake_client.pushed[0])
    assert entry["task_name"] == "app.tasks.example.do_thing"
    assert entry["task_id"] == "abc-123"
    assert entry["error"] == "boom"
