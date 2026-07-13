"""UTC business-clock port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class UtcClock(Protocol):
    """Provide an explicitly injected UTC business instant."""

    def now(self) -> datetime: ...
