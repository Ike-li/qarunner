"""Fake cron schedule port for testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qarunner.models import TestSchedule


@dataclass
class FakeSchedulePort:
    """Records schedule registrations without running a real cron engine."""

    started: bool = False
    shutdown_called: bool = False
    upserted: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    async def start(self) -> None:
        self.started = True

    def upsert(self, schedule: TestSchedule) -> None:
        self.upserted.append(schedule.id)

    def remove(self, schedule_id: str) -> None:
        self.removed.append(schedule_id)

    async def shutdown(self) -> None:
        self.shutdown_called = True
