"""Real task scheduler using asyncio primitives."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class AsyncioScheduler:
    """Schedule background coroutines with bounded concurrency."""

    def __init__(self, max_concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def schedule(self, coro: Coroutine[Any, Any, None]) -> None:
        asyncio.create_task(self._run(coro))

    async def _run(self, coro: Coroutine[Any, Any, None]) -> None:
        async with self._semaphore:
            await coro
