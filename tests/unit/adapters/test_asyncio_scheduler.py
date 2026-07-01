"""Tests for AsyncioScheduler — persistent poller with SQLite-backed queue."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from qarunner.adapters.asyncio_scheduler import AsyncioScheduler


# ── minimal fake store for poller tests ──────────────────────────────────────


@dataclass
class _FakeStore:
    """Store with a controllable dequeue_next_queued() for scheduler tests."""

    _queued: list[str] = field(default_factory=list)
    dequeued: list[str] = field(default_factory=list)

    async def dequeue_next_queued(self) -> str | None:
        if not self._queued:
            return None
        run_id = self._queued.pop(0)
        self.dequeued.append(run_id)
        return run_id


# ── lifecycle ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_and_shutdown() -> None:
    store = _FakeStore()
    executed: list[str] = []

    async def run_fn(run_id: str) -> None:
        executed.append(run_id)

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    assert not scheduler._running

    await scheduler.start()
    assert scheduler._running

    await scheduler.shutdown()
    assert not scheduler._running


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    store = _FakeStore()
    scheduler = AsyncioScheduler(
        store=store, run_fn=lambda _: asyncio.sleep(0), max_concurrency=1
    )
    await scheduler.start()
    await scheduler.start()  # second call is a no-op
    assert scheduler._running
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_start_raises_when_run_fn_not_set() -> None:
    scheduler = AsyncioScheduler(store=_FakeStore(), max_concurrency=1)
    # _run_fn is None because we used the no-store constructor path
    # — we need to simulate the case where set_run_fn was never called.
    # Construct without run_fn and verify start() raises.
    s = AsyncioScheduler.__new__(AsyncioScheduler)
    s._store = _FakeStore()
    s._semaphore = asyncio.Semaphore(1)
    s._tasks = set()
    s._keyed = {}
    s._poller_task = None
    s._running = False
    s._wake_event = asyncio.Event()
    s._run_fn = None

    with pytest.raises(RuntimeError, match="run_fn not set"):
        await s.start()


# ── enqueue & poller ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poller_dequeues_and_executes() -> None:
    store = _FakeStore(_queued=["run-1", "run-2"])
    executed: list[str] = []
    done_events: list[asyncio.Event] = []

    async def run_fn(run_id: str) -> None:
        executed.append(run_id)
        ev = asyncio.Event()
        done_events.append(ev)
        await ev.wait()

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=4)
    await scheduler.start()

    # Wake poller — it should dequeue and start executing
    scheduler.enqueue("run-1")
    await asyncio.sleep(0.05)
    # With 2 queued runs and 4 slots, both should be picked up
    scheduler.enqueue("run-2")
    await asyncio.sleep(0.05)

    assert store.dequeued == ["run-1", "run-2"]
    assert sorted(executed) == ["run-1", "run-2"]

    # Release the tasks
    for ev in done_events:
        ev.set()
    await scheduler.drain()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency() -> None:
    """Only max_concurrency tasks execute at a time."""
    store = _FakeStore(_queued=[f"run-{i}" for i in range(6)])
    current = 0
    max_seen = 0
    release_events: list[asyncio.Event] = []

    async def run_fn(run_id: str) -> None:
        nonlocal current, max_seen
        current += 1
        if current > max_seen:
            max_seen = current
        ev = asyncio.Event()
        release_events.append(ev)
        await ev.wait()
        current -= 1

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    await scheduler.start()
    # Enqueue to wake poller
    scheduler.enqueue("run-0")
    await asyncio.sleep(0.1)

    assert max_seen <= 2
    assert max_seen >= 2  # at least 2 should have been concurrent

    # Release all
    for ev in release_events:
        ev.set()
    await scheduler.drain()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_poller_sleeps_when_queue_empty() -> None:
    """Poller releases semaphore and waits when no queued runs."""
    store = _FakeStore()  # empty
    executed: list[str] = []

    async def run_fn(run_id: str) -> None:
        executed.append(run_id)

    scheduler = AsyncioScheduler(
        store=store, run_fn=run_fn, max_concurrency=2, poll_interval=0.05
    )
    await scheduler.start()
    # No enqueued runs — poller should sleep, not crash
    await asyncio.sleep(0.1)
    assert executed == []

    # Now enqueue one — it should be picked up on next wake or poll cycle
    store._queued.append("run-late")
    scheduler.enqueue("run-late")
    await asyncio.sleep(0.15)  # > poll_interval
    assert "run-late" in executed

    await scheduler.shutdown()


# ── cancel ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_running_task() -> None:
    """cancel(key) cancels an in-flight asyncio Task."""
    store = _FakeStore(_queued=["run-1"])
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run_fn(run_id: str) -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    await scheduler.start()
    scheduler.enqueue("run-1")
    await started.wait()

    assert scheduler.cancel("run-1") is True
    await asyncio.sleep(0.05)
    assert cancelled.is_set()

    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_cancel_unknown_key_returns_false() -> None:
    store = _FakeStore()
    scheduler = AsyncioScheduler(store=store, run_fn=lambda _: asyncio.sleep(0), max_concurrency=1)
    assert scheduler.cancel("nope") is False


@pytest.mark.asyncio
async def test_cancel_queued_not_running_returns_false() -> None:
    """cancel() is for in-flight tasks only; QUEUED runs are cancelled via DB."""
    store = _FakeStore(_queued=["run-1"])
    scheduler = AsyncioScheduler(
        store=store, run_fn=lambda _: asyncio.sleep(0), max_concurrency=1
    )
    # Before the poller picks up run-1, cancel() should return False
    # (no live task to cancel — orchestrator handles DB update)
    assert scheduler.cancel("run-1") is False


# ── drain ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drain_waits_for_inflight_task() -> None:
    store = _FakeStore(_queued=["run-1"])
    done: list[str] = []

    async def run_fn(run_id: str) -> None:
        await asyncio.sleep(0.05)
        done.append(run_id)

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    await scheduler.start()
    scheduler.enqueue("run-1")
    await asyncio.sleep(0.01)  # let poller pick it up
    await scheduler.drain()
    assert done == ["run-1"]


@pytest.mark.asyncio
async def test_drain_with_no_tasks_is_noop() -> None:
    store = _FakeStore()
    scheduler = AsyncioScheduler(store=store, run_fn=lambda _: asyncio.sleep(0), max_concurrency=1)
    await scheduler.drain(timeout=0.01)


@pytest.mark.asyncio
async def test_drain_cancels_task_exceeding_timeout() -> None:
    store = _FakeStore(_queued=["run-1"])
    started = asyncio.Event()
    completed: list[str] = []

    async def run_fn(run_id: str) -> None:
        started.set()
        await asyncio.sleep(10)
        completed.append(run_id)

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    await scheduler.start()
    scheduler.enqueue("run-1")
    await started.wait()
    await scheduler.drain(timeout=0.05)
    await asyncio.sleep(0)
    assert completed == []

    await scheduler.shutdown()


# ── done-callback cleanup ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_task_is_dereferenced() -> None:
    store = _FakeStore(_queued=["run-1"])

    async def run_fn(run_id: str) -> None:
        return None

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    await scheduler.start()
    scheduler.enqueue("run-1")
    await scheduler.drain()
    assert scheduler._tasks == set()
    assert scheduler._keyed == {}
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_failing_task_is_dereferenced() -> None:
    store = _FakeStore(_queued=["run-1"])

    async def run_fn(run_id: str) -> None:
        raise RuntimeError("boom")

    scheduler = AsyncioScheduler(store=store, run_fn=run_fn, max_concurrency=2)
    await scheduler.start()
    scheduler.enqueue("run-1")
    await scheduler.drain()
    assert scheduler._tasks == set()
    await scheduler.shutdown()


@pytest.mark.asyncio
async def test_poller_handles_dequeue_exception() -> None:
    """Poller catches exceptions from dequeue and keeps running."""
    store = _FakeStore()

    call_count = 0

    async def faulty_dequeue() -> str | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("db explode")
        return "run-after-error"

    store.dequeue_next_queued = faulty_dequeue  # type: ignore[method-assign]
    executed: list[str] = []

    async def run_fn(run_id: str) -> None:
        executed.append(run_id)

    scheduler = AsyncioScheduler(
        store=store, run_fn=run_fn, max_concurrency=2, poll_interval=0.05
    )
    await scheduler.start()
    scheduler.enqueue("any")
    await asyncio.sleep(0.2)  # poller recovers and picks up run-after-error

    await scheduler.shutdown()
    # The poller survived the exception and dequeued a run on the next cycle.
    assert "run-after-error" in executed
