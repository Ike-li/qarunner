"""In-memory run store for testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from qarunner.errors import RunNotFound
from qarunner.models import Run, TestCaseResult


@dataclass
class InMemoryRunStore:
    """RunStore backed by a plain dict."""

    _runs: dict[str, Run] = field(default_factory=dict)
    _cases: dict[str, list[TestCaseResult]] = field(default_factory=dict)

    async def save(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    async def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    async def save_cases(
        self,
        run_id: str,
        tests_path: str,
        created_at: datetime,
        cases: list[TestCaseResult],
    ) -> None:
        # Idempotent: a re-persist overwrites rather than appends.
        self._cases[run_id] = list(cases)

    async def get_cases_for_run(self, run_id: str) -> list[TestCaseResult]:
        return list(self._cases.get(run_id, []))
