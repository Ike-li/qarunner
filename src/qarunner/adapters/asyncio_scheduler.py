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
        # run_id -> task, so a specific in-flight execution can be cancelled.
        self._keyed: dict[str, asyncio.Task[None]] = {}

    def schedule(self, coro: Coroutine[Any, Any, None], *, key: str | None = None) -> None:
        task = asyncio.create_task(self._run(coro))
        self._tasks.add(task)
        if key is not None:
            self._keyed[key] = task
        task.add_done_callback(self._on_task_done)

    def cancel(self, key: str) -> bool:
        """Cancel a scheduled task by *key* (a run id).

        Returns True if a live (not-yet-done) task was found and cancelled.
        Cancellation propagates ``CancelledError`` into the running coroutine,
        whose own cleanup (kill subprocess / stop container, persist the
        terminal state) then runs.
        """
        task = self._keyed.get(key)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        # Drop the keyed reference too (linear scan; the keyed map only holds
        # in-flight run executions, so it stays small).
        for key, keyed_task in list(self._keyed.items()):
            if keyed_task is task:
                del self._keyed[key]
                break
        # Guard cancelled() first: calling exception() on a cancelled task raises.
        if not task.cancelled() and task.exception() is not None:
            logger.error("scheduled task failed", exc_info=task.exception())

    async def _run(self, coro: Coroutine[Any, Any, None]) -> None:
        async with self._semaphore:
            await coro

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for in-flight scheduled tasks to finish before shutdown (DATA-4).

        Lets each running execution persist its terminal state while the store
        is still open. Any task still pending after *timeout* seconds is
        cancelled and awaited, so none is left to ``save`` against a closed
        connection. ``timeout=None`` waits indefinitely.
        """
        pending = [t for t in self._tasks if not t.done()]
        if not pending:
            return
        _, still_pending = await asyncio.wait(pending, timeout=timeout)
        if still_pending:
            logger.warning(
                "drain timed out; cancelling %d in-flight task(s)", len(still_pending)
            )
            for task in still_pending:
                task.cancel()
            await asyncio.gather(*still_pending, return_exceptions=True)
