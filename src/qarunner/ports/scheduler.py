"""Task scheduling port."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskScheduler(Protocol):
    """Port for scheduling background coroutines."""

    def schedule(self, coro: Coroutine[Any, Any, None], *, key: str | None = None) -> None: ...

    def cancel(self, key: str) -> bool:
        """Cancel a scheduled task by *key*.

        Returns True if a live (not-yet-done) task was found and cancelled.
        """
        ...

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for in-flight scheduled tasks to finish (graceful shutdown).

        Tasks still running after *timeout* seconds are cancelled; ``None``
        waits indefinitely.
        """
        ...
