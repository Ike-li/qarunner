"""Real task scheduler using asyncio primitives."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class AsyncioScheduler:
    """Schedule background coroutines with bounded concurrency.

    Scheduled tasks are kept in a set to retain a strong reference: the event
    loop only holds a weak reference, so an untracked task may be garbage
    collected mid-flight and silently cancelled. A done-callback drops the
    reference once finished and surfaces any unhandled exception through the
    logger instead of letting it vanish.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(self._run(coro))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        # Guard cancelled() first: calling exception() on a cancelled task raises.
        if not task.cancelled() and task.exception() is not None:
            logger.error("scheduled task failed", exc_info=task.exception())

    async def _run(self, coro: Coroutine[Any, Any, None]) -> None:
        async with self._semaphore:
            await coro
