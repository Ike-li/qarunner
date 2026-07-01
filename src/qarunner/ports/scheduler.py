"""Task scheduling port.

``TaskScheduler`` controls bounded concurrency for test-run execution.

``enqueue(run_id)`` signals that a run has been persisted as QUEUED and is
ready for the poller to pick up.

``cancel(key)`` cancels an **in-flight** asyncio Task only. For QUEUED runs
that haven't been dequeued yet, the caller (orchestrator) must update the
store directly (SET status='cancelled') — this method does NOT touch the DB.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskScheduler(Protocol):
    """Port for scheduling background coroutines with a persistent queue."""

    async def start(self) -> None:
        """Start the background poller loop."""
        ...

    def enqueue(self, run_id: str) -> None:
        """Signal that *run_id* is ready for execution (persisted as QUEUED).

        The poller will atomically dequeue the next QUEUED run from the store
        when a concurrency slot opens.
        """
        ...

    def cancel(self, key: str) -> bool:
        """Cancel an in-flight execution by *key* (a run id).

        Only cancels the asyncio Task for a currently-executing run — does
        NOT touch the DB.  For QUEUED runs that haven't been picked up yet,
        the caller MUST update the store directly.

        Returns True if a live (not-yet-done) task was found and cancelled.
        """
        ...

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for in-flight scheduled tasks to finish (graceful shutdown).

        Tasks still running after *timeout* seconds are cancelled; ``None``
        waits indefinitely.
        """
        ...

    async def shutdown(self) -> None:
        """Stop the background poller loop.

        Does NOT drain in-flight tasks — call ``drain()`` after ``shutdown()``
        if you need to wait for them.
        """
        ...
