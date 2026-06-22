"""Fake task scheduler for testing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class FakeScheduler:
    """Scheduler that runs coroutines inline and tracks the count."""

    scheduled: int = 0
    drained: int = 0

    def schedule(self, coro: Any) -> None:
        self.scheduled += 1
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async test — create a task so it runs in the loop.
            loop.create_task(coro)
        else:
            asyncio.run(coro)

    async def drain(self, timeout: float | None = None) -> None:
        """Record a drain request; inline execution leaves nothing in flight."""
        self.drained += 1
