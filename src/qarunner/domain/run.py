"""Run aggregate for the greenfield scheduling lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.assignment import Assignment, AssignmentState
from qarunner.domain.attempt import Attempt
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import (
    AssignmentConflict,
    IdempotencyConflict,
    InvalidTransition,
    ensure_expected_version,
)
from qarunner.domain.worker import WorkerRef


class RunState(enum.StrEnum):
    """States required before a Run may own an Assignment."""

    PLANNED = "planned"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"


_ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PLANNED: frozenset({RunState.QUEUED}),
    RunState.QUEUED: frozenset(),
    RunState.ASSIGNED: frozenset(),
    RunState.RUNNING: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CommitStartResult:
    """Durable identity returned before a Worker creates an execution sandbox."""

    run: Run
    attempt: Attempt
    fence: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class Run:
    """Immutable Run state; Assignment/Attempt semantics are added incrementally."""

    id: str
    state: RunState
    version: int
    current_fence: int
    assignment: Assignment | None
    attempts: tuple[Attempt, ...]

    @classmethod
    def create(cls, *, run_id: str) -> Run:
        """Create a planned Run."""
        return cls(
            id=run_id,
            state=RunState.PLANNED,
            version=0,
            current_fence=0,
            assignment=None,
            attempts=(),
        )

    def transition(self, target: RunState, *, expected_version: int) -> Run:
        """Reject stale commands or state edges that skip scheduling phases."""
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="run",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(self, state=target, version=self.version + 1)

    def offer_assignment(
        self,
        *,
        assignment_id: str,
        worker: WorkerRef,
        spec_digest: Digest,
        expected_version: int,
    ) -> Run:
        """Reserve a queued Run for one Worker generation."""
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if self.state != RunState.QUEUED or self.assignment is not None:
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason="run_not_queued",
            )
        assignment = Assignment.offer(
            assignment_id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
        )
        return replace(
            self,
            state=RunState.ASSIGNED,
            assignment=assignment,
            version=self.version + 1,
        )

    def claim_assignment(
        self,
        *,
        assignment_id: str,
        worker: WorkerRef,
        expected_version: int,
    ) -> Run:
        """Record that the bound Worker generation accepted its offer."""
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if (
            self.state != RunState.ASSIGNED
            or self.assignment is None
            or self.assignment.id != assignment_id
            or self.assignment.worker != worker
            or self.assignment.state != AssignmentState.OFFERED
        ):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason="offer_mismatch",
            )
        return replace(
            self,
            assignment=self.assignment.claim(),
            version=self.version + 1,
        )

    def commit_start(
        self,
        *,
        assignment_id: str,
        worker: WorkerRef,
        start_commit_key: str,
        spec_digest: Digest,
        new_attempt_id: str,
        expected_version: int,
    ) -> CommitStartResult:
        """Create the first durable Attempt/fence for a claimed Assignment."""
        received_digest = _start_commit_digest(
            assignment_id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
        )
        existing = next(
            (attempt for attempt in self.attempts if attempt.start_commit_key == start_commit_key),
            None,
        )
        if existing is not None:
            stored_digest = _start_commit_digest(
                assignment_id=existing.assignment_id,
                worker=existing.worker,
                spec_digest=existing.spec_digest,
            )
            if stored_digest != received_digest:
                raise IdempotencyConflict(
                    scope=f"run:{self.id}:start-commit",
                    key=start_commit_key,
                    stored_digest=stored_digest,
                    received_digest=received_digest,
                )
            return CommitStartResult(
                run=self,
                attempt=existing,
                fence=existing.fence,
                replayed=True,
            )
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if (
            self.state != RunState.ASSIGNED
            or self.assignment is None
            or self.assignment.id != assignment_id
            or self.assignment.worker != worker
            or self.assignment.spec_digest != spec_digest
            or self.assignment.state != AssignmentState.CLAIMED
        ):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason="assignment_not_claimed",
            )
        fence = self.current_fence + 1
        attempt = Attempt.create(
            attempt_id=new_attempt_id,
            run_id=self.id,
            attempt_no=len(self.attempts) + 1,
            fence=fence,
            assignment_id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
            start_commit_key=start_commit_key,
        )
        committed_run = replace(
            self,
            state=RunState.RUNNING,
            version=self.version + 1,
            current_fence=fence,
            assignment=self.assignment.commit(),
            attempts=(*self.attempts, attempt),
        )
        return CommitStartResult(
            run=committed_run,
            attempt=attempt,
            fence=fence,
            replayed=False,
        )


def _start_commit_digest(*, assignment_id: str, worker: WorkerRef, spec_digest: Digest) -> Digest:
    """Bind replay identity to Assignment, Worker generation, and execution spec."""
    return canonical_digest(
        schema_version="qep.start-commit.v1",
        payload={
            "assignment_id": assignment_id,
            "worker_id": worker.worker_id,
            "worker_generation": worker.generation,
            "spec_digest": spec_digest.value,
        },
    )
