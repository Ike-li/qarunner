"""Run storage port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qarunner.models import Run


@runtime_checkable
class RunStore(Protocol):
    """Port for persisting and retrieving Run records."""

    async def save(self, run: Run) -> None: ...

    async def get(self, run_id: str) -> Run:
        """Return the run or raise RunNotFound."""
        ...

    async def list(self) -> list[Run]: ...
