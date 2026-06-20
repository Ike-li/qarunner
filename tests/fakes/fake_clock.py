"""Fake clock for testing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class FakeClock:
    """Clock that returns a mutable `.current` datetime."""

    current: datetime = datetime(2025, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current
