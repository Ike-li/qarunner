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
