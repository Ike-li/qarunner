"""In-memory `AssignmentGateway` test double for unit-testing the command flow.

Deliberately simple: it stores Runs by id and persists on publish. Real optimistic-CAS,
`qep_assignments_one_active_per_run` exclusivity, monotonic fence, and idempotent replay
are the PostgreSQL adapter's job and are proven against real PostgreSQL in the
`ASGN-OFFER`/`ASGN-CLAIM`/`ASGN-COMMIT-START`/`ASGN-CLOSE` sub-gates — this Fake only lets
the application-layer offer/claim/commit-start/close commands be exercised in isolation.
"""

from __future__ import annotations

from qarunner.application.ports.assignment import (
    AssignmentGateway,
    AssignmentMutationSnapshot,
)
from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain import Run
from qarunner.domain.run import CommitStartResult


class InMemoryAssignmentGateway(AssignmentGateway):
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._commits: dict[str, CommitStartResult] = {}

    def seed(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get_run_for_update(self, *, run_id: str) -> AssignmentMutationSnapshot:
        run = self._runs.get(run_id)
        if run is None:
            raise PortContractError(
                resource="assignment_gateway", field="run_id", reason="not_found"
            )
        return AssignmentMutationSnapshot(run=run)

    async def publish_offer(
        self, *, offered: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        return self._persist_run(offered, expected)

    async def publish_claim(
        self, *, claimed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        return self._persist_run(claimed, expected)

    async def publish_close(
        self, *, closed: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        return self._persist_run(closed, expected)

    async def publish_commit_start(
        self, *, commit: CommitStartResult, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[CommitStartResult]:
        self._require_expected(expected)
        self._runs[commit.run.id] = commit.run
        self._commits[commit.attempt.start_commit_key] = commit
        return ReplayResult(value=commit, replayed=False)

    def _persist_run(
        self, mutated: Run, expected: AssignmentMutationSnapshot
    ) -> ReplayResult[Run]:
        self._require_expected(expected)
        self._runs[mutated.id] = mutated
        return ReplayResult(value=mutated, replayed=False)

    def _require_expected(self, expected: AssignmentMutationSnapshot) -> None:
        current = self._runs.get(expected.run_id)
        if current is None or current.version != expected.version:
            raise PortContractError(
                resource="assignment_gateway", field="expected", reason="stale_snapshot"
            )
