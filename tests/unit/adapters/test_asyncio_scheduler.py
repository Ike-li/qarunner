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
