"""In-memory run store for testing."""

from __future__ import annotations

from dataclasses import dataclass, field

from qarunner.errors import RunNotFound
from qarunner.models import Run


@dataclass
class InMemoryRunStore:
    """RunStore backed by a plain dict."""

    _runs: dict[str, Run] = field(default_factory=dict)

    async def save(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    async def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
