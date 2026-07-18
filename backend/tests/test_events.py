import asyncio

from app.shared import events as events_module


def test_persist_best_effort_write_is_lost_without_flush(monkeypatch):
    """Documents the bug this session found and fixed: a fire-and-forget
    persistence task scheduled via create_task() is silently cancelled the
    moment asyncio.run()'s coroutine returns, unless something awaits it
    first. Plain `emit()` alone — the way every Celery task called it before
    the fix — does not do that."""
    persisted = []

    async def fake_persist(event_name, payload):
        await asyncio.sleep(0.05)
        persisted.append(event_name)

    monkeypatch.setattr(events_module, "_persist", fake_persist)

    async def _body():
        events_module._persist_best_effort("some.event", {})
        # Returns immediately, before fake_persist's sleep resolves — mirrors
        # a Celery task's asyncio.run(_task_body()) returning right after
        # emit() schedules the task but before it's had a chance to run.

    asyncio.run(_body())

    assert persisted == []  # the write never happened — this is the bug


def test_run_with_event_flush_waits_for_persistence(monkeypatch):
    persisted = []

    async def fake_persist(event_name, payload):
        await asyncio.sleep(0.05)
        persisted.append(event_name)

    monkeypatch.setattr(events_module, "_persist", fake_persist)

    async def _body():
        events_module._persist_best_effort("some.event", {})
        return "task result"

    result = events_module.run_with_event_flush(_body())

    assert result == "task result"
    assert persisted == ["some.event"]
