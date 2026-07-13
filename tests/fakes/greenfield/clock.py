"""Deterministic UTC clock Fake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.application.ports.common import ensure_utc


@dataclass(slots=True)
class FakeUtcClock:
    """Return only the explicitly supplied business instant."""

    current: datetime

    def __post_init__(self) -> None:
        ensure_utc(resource="clock", field="current", value=self.current)

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        self.current = ensure_utc(resource="clock", field="current", value=value)

    def advance(self, delta: timedelta) -> None:
        self.current += delta
