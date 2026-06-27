"""Tests for AsyncioScheduler adapter."""

from __future__ import annotations

import asyncio

from qarunner.adapters.asyncio_scheduler import AsyncioScheduler


async def test_schedule_runs_coroutine() -> None:
    results: list[int] = []

    async def task() -> None:
        results.append(42)

    scheduler = AsyncioScheduler()
    scheduler.schedule(task())
    await asyncio.sleep(0.05)
    assert results == [42]


async def test_schedule_multiple_tasks() -> None:
    results: list[int] = []

    async def task(val: int) -> None:
        results.append(val)

    scheduler = AsyncioScheduler()
    scheduler.schedule(task(1))
    scheduler.schedule(task(2))
    scheduler.schedule(task(3))
    await asyncio.sleep(0.1)
    assert sorted(results) == [1, 2, 3]


async def test_semaphore_limits_concurrency() -> None:
    max_seen = 0
    current = 0

    async def task() -> None:
        nonlocal max_seen, current
        current += 1
        if current > max_seen:
            max_seen = current
        await asyncio.sleep(0.05)
        current -= 1

    scheduler = AsyncioScheduler(max_concurrency=2)
    for _ in range(6):
        scheduler.schedule(task())
    await asyncio.sleep(0.5)
    assert max_seen <= 2


async def test_completed_task_is_dereferenced() -> None:
    scheduler = AsyncioScheduler()

    async def task() -> None:
        return None

    scheduler.schedule(task())
    await asyncio.sleep(0.05)
    # Strong reference is dropped once the task finishes cleanly.
    assert scheduler._tasks == set()


async def test_failing_task_is_logged_not_raised() -> None:
    scheduler = AsyncioScheduler()

    async def boom() -> None:
        raise RuntimeError("kaboom")

    # Must not propagate out of the scheduler / crash the loop.
    scheduler.schedule(boom())
    await asyncio.sleep(0.05)
    assert scheduler._tasks == set()


async def test_cancelled_task_is_handled() -> None:
    scheduler = AsyncioScheduler()
    started = asyncio.Event()

    async def long_task() -> None:
        started.set()
        await asyncio.sleep(10)

    scheduler.schedule(long_task())
    await started.wait()
    task = next(iter(scheduler._tasks))
    task.cancel()
    await asyncio.sleep(0.05)
    # Cancellation is handled without calling .exception() (which would raise).
    assert scheduler._tasks == set()


async def test_drain_waits_for_inflight_task() -> None:
    scheduler = AsyncioScheduler()
    done: list[int] = []

    async def task() -> None:
        await asyncio.sleep(0.05)
        done.append(1)

    scheduler.schedule(task())
    await scheduler.drain()
    # drain returns only after the in-flight task has run to completion.
    assert done == [1]
    assert scheduler._tasks == set()


async def test_drain_with_no_tasks_is_noop() -> None:
    scheduler = AsyncioScheduler()
    # Nothing scheduled — drain returns immediately without error.
    await scheduler.drain(timeout=0.01)
    assert scheduler._tasks == set()


async def test_drain_cancels_task_exceeding_timeout() -> None:
    scheduler = AsyncioScheduler()
    started = asyncio.Event()
    completed: list[int] = []

    async def hung_task() -> None:
        started.set()
        await asyncio.sleep(10)
        completed.append(1)  # never reached — cancelled by drain first

    scheduler.schedule(hung_task())
    await started.wait()
    await scheduler.drain(timeout=0.05)
    await asyncio.sleep(0)  # let the done-callback discard the cancelled task
    # The hung task is cancelled before finishing; its side effect never runs.
    assert completed == []
    assert scheduler._tasks == set()


async def test_cancel_by_key_cancels_running_task() -> None:
    scheduler = AsyncioScheduler()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_task() -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    scheduler.schedule(long_task(), key="run-1")
    await started.wait()
    assert scheduler.cancel("run-1") is True
    await asyncio.sleep(0.05)
    assert cancelled.is_set()
    # Both references are dropped once the cancelled task settles.
    assert scheduler._tasks == set()
    assert scheduler._keyed == {}


async def test_cancel_unknown_key_returns_false() -> None:
    scheduler = AsyncioScheduler()
    assert scheduler.cancel("nope") is False


async def test_cancel_completed_key_returns_false() -> None:
    scheduler = AsyncioScheduler()

    async def quick() -> None:
        return None

    scheduler.schedule(quick(), key="run-2")
    await asyncio.sleep(0.05)
    # The task finished and was dereferenced; nothing live to cancel.
    assert scheduler.cancel("run-2") is False


async def test_unkeyed_schedule_is_not_cancellable() -> None:
    scheduler = AsyncioScheduler()
    started = asyncio.Event()

    async def long_task() -> None:
        started.set()
        await asyncio.sleep(10)

    scheduler.schedule(long_task())  # no key
    await started.wait()
    assert scheduler.cancel("anything") is False
    # Clean up the still-running task so the test doesn't leak it.
    next(iter(scheduler._tasks)).cancel()
    await asyncio.sleep(0.01)


async def test_keyed_cleanup_scans_past_non_matching_tasks() -> None:
    scheduler = AsyncioScheduler()
    release = asyncio.Event()

    async def keep_running() -> None:
        await release.wait()

    async def quick() -> None:
        return None

    # "slow" is inserted first, "fast" second; when "fast" finishes its done
    # callback scans past the non-matching "slow" entry to reach its own.
    scheduler.schedule(keep_running(), key="slow")
    scheduler.schedule(quick(), key="fast")
    await asyncio.sleep(0.05)
    assert "fast" not in scheduler._keyed
    assert "slow" in scheduler._keyed  # still running, reference retained
    release.set()
    await asyncio.sleep(0.05)
    assert scheduler._keyed == {}
