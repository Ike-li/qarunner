"""In-memory run store for testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from qarunner.core.flaky import FlakyPolicy, flakiness
from qarunner.errors import RunNotFound
from qarunner.models import CaseHistoryPoint, FailureDiagnosis, Run, RunStatus, TestCaseResult


@dataclass
class InMemoryRunStore:
    """RunStore backed by a plain dict."""

    _runs: dict[str, Run] = field(default_factory=dict)
    _cases: dict[str, list[TestCaseResult]] = field(default_factory=dict)
    _ai_diagnoses: dict[str, FailureDiagnosis] = field(default_factory=dict)

    async def save(self, run: Run) -> None:
        self._runs[run.id] = run

    async def create_if_below_inflight_limit(self, run: Run, limit: int) -> bool:
        inflight = sum(
            1
            for stored in self._runs.values()
            if stored.created_by == run.created_by
            and stored.status in (RunStatus.QUEUED, RunStatus.RUNNING)
        )
        if inflight >= limit:
            return False
        self._runs[run.id] = run
        return True

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

    async def save_ai_diagnosis(self, run_id, diagnosis, provider, model, created_at):
        self._ai_diagnoses[run_id] = diagnosis

    async def get_ai_diagnosis(self, run_id):
        return self._ai_diagnoses.get(run_id)

    async def get_case_history(
        self,
        tests_path,
        suite,
        name,
        limit=20,
        created_by=None,
    ):
        rows = []
        for run_id, cases in self._cases.items():
            run = self._runs.get(run_id)
            if run is None or run.tests_path != tests_path:
                continue
            if created_by is not None and run.created_by != created_by:
                continue
            for c in cases:
                if c.suite == suite and c.name == name:
                    rows.append((run.created_at, c.status))
        rows.sort(key=lambda x: x[0], reverse=True)
        return [CaseHistoryPoint(created_at=ca, status=st) for ca, st in reversed(rows[:limit])]

    async def dequeue_next_queued(self) -> str | None:
        """Find the oldest QUEUED run and advance it to RUNNING."""
        queued = sorted(
            [r for r in self._runs.values() if r.status == RunStatus.QUEUED],
            key=lambda r: r.created_at,
        )
        if not queued:
            return None
        run = queued[0]
        run = run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "started_at": datetime.now(UTC),
            }
        )
        self._runs[run.id] = run
        return run.id

    async def cancel_if_inflight(self, run_id: str, finished_at: str) -> bool:
        """BUG-3: atomically set CANCELLED only if still QUEUED or RUNNING."""
        run = self._runs.get(run_id)
        if run is None:
            return False
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            self._runs[run_id] = run.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "finished_at": datetime.fromisoformat(finished_at),
                }
            )
            return True
        return False

    async def count_flaky_tests(
        self,
        days: int = 30,
        created_by: str | None = None,
        *,
        min_observations: int = 4,
        flip_threshold: int = 3,
    ) -> int:
        """Count unique test cases matching the configured flaky policy."""
        from datetime import timedelta

        since = datetime.now(UTC) - timedelta(days=days)
        # Group statuses by (tests_path, suite, name).
        grouped: dict[tuple[str, str, str], list[str]] = {}
        for run_id, cases in self._cases.items():
            run = self._runs.get(run_id)
            if run is None or run.status != RunStatus.COMPLETED:
                continue
            if run.created_at < since:
                continue
            if created_by is not None and run.created_by != created_by:
                continue
            for c in cases:
                key = (run.tests_path, c.suite, c.name)
                grouped.setdefault(key, []).append(c.status)
        policy = FlakyPolicy(
            min_observations=min_observations,
            flip_threshold=flip_threshold,
        )
        return sum(1 for statuses in grouped.values() if flakiness(statuses, policy)[0])
