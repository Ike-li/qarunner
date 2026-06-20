"""Real clock adapter using system time."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Clock that returns the current UTC time."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)
