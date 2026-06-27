"""Fake task scheduler for testing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeScheduler:
    """Scheduler that runs coroutines inline and tracks the count."""

    scheduled: int = 0
    drained: int = 0
    cancelled_keys: list[str] = field(default_factory=list)
    _keyed: dict[str, Any] = field(default_factory=dict)

    def schedule(self, coro: Any, *, key: str | None = None) -> None:
        self.scheduled += 1
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async test — create a task so it runs in the loop.
            task = loop.create_task(coro)
            if key is not None:
                self._keyed[key] = task
        else:
            asyncio.run(coro)

    def cancel(self, key: str) -> bool:
        self.cancelled_keys.append(key)
        task = self._keyed.get(key)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def drain(self, timeout: float | None = None) -> None:
        """Record a drain request; inline execution leaves nothing in flight."""
        self.drained += 1
