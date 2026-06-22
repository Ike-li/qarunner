"""Cron schedule port."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qarunner.models import TestSchedule


@runtime_checkable
class SchedulePort(Protocol):
    """Port for registering test schedules on a background cron engine.

    An implementation owns the engine that fires a run when each schedule's
    cron expression elapses. It is storage-agnostic: the fired job resolves the
    schedule/profile from the store and creates the run via the orchestrator, so
    the engine never reaches into web-framework state.
    """

    async def start(self) -> None:
        """Load persisted schedules and start the background engine."""
        ...

    def upsert(self, schedule: TestSchedule) -> None:
        """Register or replace the job for *schedule* (removes it if disabled)."""
        ...

    def remove(self, schedule_id: str) -> None:
        """Remove the job for *schedule_id* if present."""
        ...

    async def shutdown(self) -> None:
        """Stop the background engine."""
        ...
