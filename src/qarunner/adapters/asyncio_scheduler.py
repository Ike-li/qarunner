"""Persistent scheduler backed by a SQLite queue with an asyncio poller loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, Callable

logger = logging.getLogger(__name__)


class AsyncioScheduler:
    """Schedule background coroutines with bounded concurrency via a
    SQLite-backed queue.

    ``enqueue(run_id)`` signals the orchestrator created a new QUEUED run.
    A background poller loop atomically dequeues the next QUEUED run from
    the store (FIFO) when a semaphore slot opens, and calls ``run_fn(run_id)``.

    On restart, QUEUED runs survive in the DB and are picked up automatically
    by the new poller — only RUNNING runs are marked FAILED by crash recovery.
    """

    def __init__(
        self,
        store: Any,  # RunStore with dequeue_next_queued
        run_fn: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        max_concurrency: int = 4,
        poll_interval: float = 0.5,
    ) -> None:
        self._store = store
        self._run_fn = run_fn  # orchestrator.execute (set via set_run_fn)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()
        self._keyed: dict[str, asyncio.Task[None]] = {}
        self._poller_task: asyncio.Task[None] | None = None
        self._running = False
        self._poll_interval = poll_interval
        self._wake_event = asyncio.Event()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background poller loop."""
        if self._running:
            return
        if self._run_fn is None:
            raise RuntimeError(
                "run_fn not set — call set_run_fn() before start()"
            )
        self._running = True
        self._poller_task = asyncio.create_task(self._poller_loop())
        logger.info(
            "Persistent scheduler poller started (max_concurrency=%d)",
            self._semaphore._value,
        )

    async def shutdown(self) -> None:
        """Stop the background poller loop. Does NOT drain in-flight tasks."""
        self._running = False
        self._wake_event.set()
        if self._poller_task is not None:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
            self._poller_task = None

    def set_run_fn(self, run_fn: Callable[[str], Coroutine[Any, Any, None]]) -> None:
        """Wire the execution callback (orchestrator.execute) after construction."""
        self._run_fn = run_fn

    # ── public API ────────────────────────────────────────────────────────────

    def enqueue(self, run_id: str) -> None:
        """Signal a new QUEUED run is available — wake the poller."""
        self._wake_event.set()

    def cancel(self, key: str) -> bool:
        """Cancel an in-flight execution by *key*.

        Only cancels the asyncio Task for a currently-executing run.
        QUEUED (not yet running) runs are cancelled by the orchestrator
        writing CANCELLED to the store — this method does NOT touch the DB.

        Returns True if a live task was found and cancelled.
        """
        task = self._keyed.get(key)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for in-flight scheduled tasks to finish (graceful shutdown).

        Tasks still running after *timeout* seconds are cancelled; ``None``
        waits indefinitely.
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

    # ── poller loop ───────────────────────────────────────────────────────────

    async def _poller_loop(self) -> None:
        """Continuously dequeue from DB when semaphore slots are available."""
        while self._running:
            # Wait for a semaphore slot before attempting to dequeue.
            await self._semaphore.acquire()
            try:
                run_id = await self._store.dequeue_next_queued()
                if run_id is None:
                    # No queued runs — release the slot and wait.
                    self._semaphore.release()
                    await self._sleep_or_wait()
                    continue

                # We own a slot and have a run_id — start execution.
                task = asyncio.create_task(self._run_fn(run_id))  # type: ignore[misc]
                self._tasks.add(task)
                self._keyed[run_id] = task
                task.add_done_callback(self._on_task_done)
            except Exception:
                self._semaphore.release()
                logger.exception("poller error")
                await self._sleep_or_wait()

    async def _sleep_or_wait(self) -> None:
        """Sleep until woken by enqueue() or poll_interval elapses."""
        self._wake_event.clear()
        try:
            await asyncio.wait_for(
                self._wake_event.wait(), timeout=self._poll_interval
            )
        except asyncio.TimeoutError:
            pass

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """Cleanup: drop references and release the semaphore slot."""
        self._tasks.discard(task)
        for key, keyed_task in list(self._keyed.items()):
            if keyed_task is task:
                del self._keyed[key]
                break
        # Release the semaphore so another dequeue cycle can begin.
        self._semaphore.release()
        if not task.cancelled() and task.exception() is not None:
            logger.error("scheduled task failed", exc_info=task.exception())
