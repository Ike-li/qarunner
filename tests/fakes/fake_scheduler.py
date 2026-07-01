"""Fake task scheduler for testing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeScheduler:
    """Scheduler fake for testing — records calls without real persistence.

    ``enqueue(run_id)`` only records the call. Tests that need actual
    execution should call ``await orchestrator.execute(run_id)`` directly.
    """

    enqueued: int = 0
    drained: int = 0
    cancelled_keys: list[str] = field(default_factory=list)

    _running: bool = False

    async def start(self) -> None:
        self._running = True

    async def shutdown(self) -> None:
        self._running = False

    def enqueue(self, run_id: str) -> None:
        """Record the enqueue call (no real execution)."""
        self.enqueued += 1

    def cancel(self, key: str) -> bool:
        """Record the cancel request.  In the fake, cancelling a QUEUED run
        (not yet executing) is handled by the orchestrator writing CANCELLED
        to the store, so this only needs to track the key for test assertions.
        """
        self.cancelled_keys.append(key)
        return True

    async def drain(self, timeout: float | None = None) -> None:
        """Record a drain request (nothing to await in fake)."""
        self.drained += 1
