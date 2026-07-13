"""Run aggregate for the greenfield scheduling lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from qarunner.domain.assignment import (
    Assignment,
    AssignmentClosure,
    AssignmentClosureKind,
    AssignmentState,
)
from qarunner.domain.attempt import Attempt, AttemptState
from qarunner.domain.authority import AttemptAuthority, WorkerAuthority
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import (
    AssignmentConflict,
    AttemptConflict,
    AttemptUnknownReviewRequired,
    DomainValidationError,
    IdempotencyConflict,
    InvalidTransition,
    RetryNotAllowed,
    StaleFence,
    ensure_expected_version,
)
from qarunner.domain.retry import RetryIntent, RetryProvenance
from qarunner.domain.unknown import UnknownAdjudication, UnknownObservation
from qarunner.domain.worker import WorkerGeneration, WorkerRef


class RunState(enum.StrEnum):
    """States required before a Run may own an Assignment."""

    PLANNED = "planned"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    RETRY_QUEUED = "retry_queued"


_ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PLANNED: frozenset({RunState.QUEUED}),
    RunState.QUEUED: frozenset(),
    RunState.ASSIGNED: frozenset(),
    RunState.RUNNING: frozenset(),
    RunState.RETRY_QUEUED: frozenset(),
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
    assignments: tuple[Assignment, ...]
    current_assignment_id: str | None
    attempts: tuple[Attempt, ...]
    retry_intents: tuple[RetryIntent, ...]
    pending_retry_intent_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            _invalid_run("id", "invalid")
        if not isinstance(self.state, RunState):
            _invalid_run("state", "unknown")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            _invalid_run("version", "invalid")
        if (
            isinstance(self.current_fence, bool)
            or not isinstance(self.current_fence, int)
            or self.current_fence < 0
        ):
            _invalid_run("current_fence", "invalid")
        for field in ("current_assignment_id", "pending_retry_intent_id"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                _invalid_run(field, "invalid")
        self._validate_normalized_history()
        self._validate_state_pointers()
        self._validate_assignment_epoch_tails()
        self._validate_retry_intent_consumption()

    @classmethod
    def create(cls, *, run_id: str) -> Run:
        """Create a planned Run."""
        return cls(
            id=run_id,
            state=RunState.PLANNED,
            version=0,
            current_fence=0,
            assignments=(),
            current_assignment_id=None,
            attempts=(),
            retry_intents=(),
            pending_retry_intent_id=None,
        )

    @property
    def assignment(self) -> Assignment | None:
        """Return the current reservation while retaining historical Assignments."""
        if self.current_assignment_id is None:
            return None
        return next(
            (
                assignment
                for assignment in reversed(self.assignments)
                if assignment.id == self.current_assignment_id
            ),
            None,
        )

    @property
    def pending_retry_intent(self) -> RetryIntent | None:
        """Return the unconsumed adjudicated retry intent, if any."""
        if self.pending_retry_intent_id is None:
            return None
        return next(
            (
                intent
                for intent in reversed(self.retry_intents)
                if intent.id == self.pending_retry_intent_id
            ),
            None,
        )

    def _validate_normalized_history(self) -> None:
        if not isinstance(self.assignments, tuple):
            _invalid_run("assignments", "not_tuple")
        if any(not isinstance(assignment, Assignment) for assignment in self.assignments):
            _invalid_run("assignments", "invalid_type")
        assignment_ids = tuple(assignment.id for assignment in self.assignments)
        if len(assignment_ids) != len(set(assignment_ids)):
            _invalid_run("assignments", "duplicate_id")
        assignments_by_id = {assignment.id: assignment for assignment in self.assignments}
        if self.current_assignment_id is not None:
            if self.current_assignment_id not in assignments_by_id:
                _invalid_run("current_assignment_id", "not_found")
            if not self.assignments or self.assignments[-1].id != self.current_assignment_id:
                _invalid_run("current_assignment_id", "not_latest")

        if not isinstance(self.retry_intents, tuple):
            _invalid_run("retry_intents", "not_tuple")
        if any(not isinstance(intent, RetryIntent) for intent in self.retry_intents):
            _invalid_run("retry_intents", "invalid_type")
        retry_intent_ids = tuple(intent.id for intent in self.retry_intents)
        if len(retry_intent_ids) != len(set(retry_intent_ids)):
            _invalid_run("retry_intents", "duplicate_id")
        retry_intents_by_id = {intent.id: intent for intent in self.retry_intents}
        if self.pending_retry_intent_id is not None:
            if self.pending_retry_intent_id not in retry_intents_by_id:
                _invalid_run("pending_retry_intent_id", "not_found")
            if not self.retry_intents or self.retry_intents[-1].id != self.pending_retry_intent_id:
                _invalid_run("pending_retry_intent_id", "not_latest")

        if not isinstance(self.attempts, tuple):
            _invalid_run("attempts", "not_tuple")
        if any(not isinstance(attempt, Attempt) for attempt in self.attempts):
            _invalid_run("attempts", "invalid_type")
        attempt_ids = tuple(attempt.id for attempt in self.attempts)
        if len(attempt_ids) != len(set(attempt_ids)):
            _invalid_run("attempts", "duplicate_id")
        start_commit_keys = tuple(attempt.start_commit_key for attempt in self.attempts)
        if len(start_commit_keys) != len(set(start_commit_keys)):
            _invalid_run("attempts", "duplicate_start_commit_key")
        attempts_by_id = {attempt.id: attempt for attempt in self.attempts}

        for index, attempt in enumerate(self.attempts, start=1):
            if attempt.run_id != self.id:
                _invalid_run("attempts", "run_mismatch")
            if (
                isinstance(attempt.attempt_no, bool)
                or not isinstance(attempt.attempt_no, int)
                or attempt.attempt_no != index
            ):
                _invalid_run("attempts", "attempt_no_not_contiguous")
            if (
                isinstance(attempt.fence, bool)
                or not isinstance(attempt.fence, int)
                or attempt.fence != index
            ):
                _invalid_run("attempts", "attempt_fence_not_contiguous")
            assignment = assignments_by_id.get(attempt.assignment_id)
            if assignment is None:
                _invalid_run("attempts", "assignment_missing")
            if (
                assignment.state is not AssignmentState.COMMITTED
                or assignment.worker != attempt.worker
                or assignment.spec_digest != attempt.spec_digest
            ):
                _invalid_run("attempts", "assignment_mismatch")
            if (
                attempt.unknown_observation is not None
                and assignment.committed_at is not None
                and attempt.unknown_observation.recorded_at < assignment.committed_at
            ):
                _invalid_run("attempts", "unknown_before_assignment_commit")
            if index == 1:
                if assignment.retry_intent_id is not None:
                    _invalid_run("assignments", "retry_intent_mismatch")
                continue
            provenance = attempt.retry_provenance
            if provenance is None:
                _invalid_run("attempts", "retry_provenance_missing")
            intent = retry_intents_by_id.get(provenance.retry_intent_id)
            source = attempts_by_id.get(provenance.source_attempt_id)
            previous = self.attempts[index - 2]
            if (
                intent is None
                or source is None
                or source.id != previous.id
                or assignment.retry_intent_id != intent.id
                or attempt.spec_digest != intent.execution_spec_digest
                or intent.source_attempt_no != source.attempt_no
                or intent.source_fence != source.fence
                or provenance.retry_intent_digest != intent.digest
                or provenance.source_attempt_no != intent.source_attempt_no
                or provenance.source_fence != intent.source_fence
                or provenance.adjudication_id != intent.adjudication_id
                or provenance.adjudication_digest != intent.adjudication_digest
                or provenance.decision is not intent.decision
            ):
                _invalid_run("attempts", "retry_provenance_mismatch")

        expected_fence = self.attempts[-1].fence if self.attempts else 0
        if self.current_fence != expected_fence:
            _invalid_run("current_fence", "attempt_fence_mismatch")
        pending = self.pending_retry_intent
        if pending is not None and (
            not self.attempts or pending.source_attempt_id != self.attempts[-1].id
        ):
            _invalid_run("pending_retry_intent_id", "source_attempt_not_current")
        retry_authorities = tuple(
            (intent.source_attempt_id, intent.adjudication_id) for intent in self.retry_intents
        )
        if len(retry_authorities) != len(set(retry_authorities)):
            _invalid_run("retry_intents", "duplicate_authority")

        for intent in self.retry_intents:
            source = attempts_by_id.get(intent.source_attempt_id)
            if source is None or intent.run_id != self.id:
                _invalid_run("retry_intents", "source_mismatch")
            if (
                intent.source_attempt_no != source.attempt_no
                or intent.source_fence != source.fence
                or intent.execution_spec_digest != source.spec_digest
            ):
                _invalid_run("retry_intents", "source_mismatch")
            adjudication = source.adjudications[-1] if source.adjudications else None
            if (
                adjudication is None
                or intent.adjudication_id != adjudication.id
                or intent.adjudication_digest != adjudication.digest
                or intent.decision is not adjudication.decision
                or not adjudication.decision.permits_retry
                or intent.created_at < adjudication.occurred_at
            ):
                _invalid_run("retry_intents", "adjudication_not_authoritative")

        attempt_assignment_ids = {attempt.assignment_id for attempt in self.attempts}
        retry_intent_epochs = {
            intent.id: index for index, intent in enumerate(self.retry_intents, start=1)
        }
        assignment_epochs: list[int] = []
        for assignment in self.assignments:
            if assignment.retry_intent_id is not None:
                intent = retry_intents_by_id.get(assignment.retry_intent_id)
                if intent is None or assignment.spec_digest != intent.execution_spec_digest:
                    _invalid_run("assignments", "retry_intent_mismatch")
                if assignment.offered_at < intent.created_at:
                    _invalid_run("assignments", "offered_before_retry_intent")
                assignment_epochs.append(retry_intent_epochs[intent.id])
            else:
                assignment_epochs.append(0)
            is_current = assignment.id == self.current_assignment_id
            if assignment.state is AssignmentState.COMMITTED:
                if assignment.id not in attempt_assignment_ids:
                    _invalid_run("assignments", "committed_without_attempt")
            elif assignment.state in {
                AssignmentState.EXPIRED_PRESTART,
                AssignmentState.RELEASED_PRESTART,
            }:
                if is_current:
                    _invalid_run("assignments", "closed_reservation_current")
            elif not is_current:
                _invalid_run("assignments", "historical_reservation_not_committed")
        if self.assignments and any(
            assignment.spec_digest != self.assignments[0].spec_digest
            for assignment in self.assignments[1:]
        ):
            _invalid_run("assignments", "spec_digest_mismatch")
        committed_assignment_ids = tuple(
            assignment.id
            for assignment in self.assignments
            if assignment.state is AssignmentState.COMMITTED
        )
        attempt_assignment_order = tuple(attempt.assignment_id for attempt in self.attempts)
        if committed_assignment_ids != attempt_assignment_order:
            _invalid_run("assignments", "attempt_order_mismatch")
        if assignment_epochs != sorted(assignment_epochs):
            _invalid_run("assignments", "retry_intent_order_mismatch")
        previous_closed_at: datetime | None = None
        for assignment in self.assignments:
            if previous_closed_at is not None and assignment.offered_at < previous_closed_at:
                _invalid_run("assignments", "timeline_not_monotonic")
            previous_closed_at = (
                assignment.closure.recorded_at
                if assignment.closure is not None
                else assignment.committed_at
            )

    def _validate_state_pointers(self) -> None:
        current = self.assignment
        pending = self.pending_retry_intent
        if self.state is RunState.PLANNED:
            if self.assignments or self.attempts or self.retry_intents:
                _invalid_run("state", "planned_has_history")
        elif self.state is RunState.QUEUED:
            if current is not None:
                _invalid_run("current_assignment_id", "must_be_clear_for_queued")
            if pending is not None:
                _invalid_run("pending_retry_intent_id", "must_be_clear_for_queued")
            if self.attempts or self.retry_intents:
                _invalid_run("state", "queued_has_attempt_history")
        elif self.state is RunState.RETRY_QUEUED:
            if current is not None:
                _invalid_run(
                    "current_assignment_id",
                    "must_be_clear_for_retry_queued",
                )
            if pending is None:
                _invalid_run(
                    "pending_retry_intent_id",
                    "required_for_retry_queued",
                )
        elif self.state is RunState.ASSIGNED:
            if current is None or current.state not in {
                AssignmentState.OFFERED,
                AssignmentState.CLAIMED,
            }:
                _invalid_run("current_assignment_id", "active_assignment_required")
            if self.attempts:
                if pending is None:
                    _invalid_run(
                        "pending_retry_intent_id",
                        "required_for_retry_assignment",
                    )
                if current.retry_intent_id != pending.id:
                    _invalid_run("current_assignment_id", "retry_intent_mismatch")
        else:
            if current is None or current.state is not AssignmentState.COMMITTED:
                _invalid_run("current_assignment_id", "committed_assignment_required")
            if pending is not None:
                _invalid_run(
                    "pending_retry_intent_id",
                    "must_be_consumed_for_running",
                )

    def _validate_retry_intent_consumption(self) -> None:
        consumed_intent_ids = tuple(
            attempt.retry_provenance.retry_intent_id
            for attempt in self.attempts
            if attempt.retry_provenance is not None
        )
        expected_intent_ids = (
            consumed_intent_ids
            if self.pending_retry_intent_id is None
            else (*consumed_intent_ids, self.pending_retry_intent_id)
        )
        actual_intent_ids = tuple(intent.id for intent in self.retry_intents)
        if actual_intent_ids != expected_intent_ids:
            _invalid_run("retry_intents", "order_mismatch")

    def _validate_assignment_epoch_tails(self) -> None:
        for attempt_index, attempt in enumerate(self.attempts):
            retry_intent_id = (
                None if attempt_index == 0 else self.retry_intents[attempt_index - 1].id
            )
            group_positions = tuple(
                index
                for index, assignment in enumerate(self.assignments)
                if assignment.retry_intent_id == retry_intent_id
            )
            committed_position = next(
                index
                for index, assignment in enumerate(self.assignments)
                if assignment.id == attempt.assignment_id
            )
            if not group_positions or committed_position != group_positions[-1]:
                _invalid_run("assignments", "committed_assignment_not_epoch_tail")

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
        worker: WorkerGeneration,
        worker_authority: WorkerAuthority,
        spec_digest: Digest,
        offered_at: datetime,
        expires_at: datetime,
        expected_version: int,
    ) -> Run:
        """Reserve a queued Run for one Worker generation."""
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if (
            self.state not in {RunState.QUEUED, RunState.RETRY_QUEUED}
            or self.assignment is not None
        ):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason="run_not_queued",
            )
        if any(assignment.id == assignment_id for assignment in self.assignments):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason="assignment_id_reused",
            )
        if (
            self.state is RunState.QUEUED
            and self.assignments
            and spec_digest != self.assignments[0].spec_digest
        ):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason="spec_digest_mismatch",
            )
        retry_intent = self.pending_retry_intent
        if self.state is RunState.RETRY_QUEUED and retry_intent is None:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=self.attempts[-1].id if self.attempts else "",
                adjudication_id="",
                reason="retry_intent_not_pending",
            )
        if self.state is RunState.QUEUED and retry_intent is not None:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=retry_intent.source_attempt_id,
                adjudication_id=retry_intent.adjudication_id,
                reason="retry_intent_not_pending",
            )
        if retry_intent is not None and spec_digest != retry_intent.execution_spec_digest:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=retry_intent.source_attempt_id,
                adjudication_id=retry_intent.adjudication_id,
                reason="retry_spec_mismatch",
            )
        worker_ref = worker.claimable_ref(authority=worker_authority)
        assignment = Assignment.offer(
            assignment_id=assignment_id,
            worker=worker_ref,
            spec_digest=spec_digest,
            offered_at=offered_at,
            expires_at=expires_at,
            retry_intent_id=retry_intent.id if retry_intent is not None else None,
        )
        return replace(
            self,
            state=RunState.ASSIGNED,
            assignments=(*self.assignments, assignment),
            current_assignment_id=assignment.id,
            version=self.version + 1,
        )

    def claim_assignment(
        self,
        *,
        assignment_id: str,
        worker: WorkerRef,
        observed_at: datetime,
        expected_version: int,
    ) -> Run:
        """Record that the bound Worker generation accepted its offer."""
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        _require_utc_command_time("observed_at", observed_at)
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
        if not (self.assignment.offered_at <= observed_at < self.assignment.expires_at):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason=(
                    "assignment_expired"
                    if observed_at >= self.assignment.expires_at
                    else "assignment_not_effective"
                ),
            )
        return replace(
            self,
            assignments=self._replace_current_assignment(
                self.assignment.claim(claimed_at=observed_at)
            ),
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
        observed_at: datetime,
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
            if existing.fence != self.current_fence:
                raise StaleFence(
                    attempt_id=existing.id,
                    current_fence=self.current_fence,
                    received_fence=existing.fence,
                )
            if (
                self.state is not RunState.RUNNING
                or self.assignment is None
                or self.assignment.id != existing.assignment_id
                or self.assignment.state is not AssignmentState.COMMITTED
                or self.attempts[-1].id != existing.id
                or existing.state
                not in {
                    AttemptState.START_COMMITTED,
                    AttemptState.PROVISIONING,
                    AttemptState.RUNNING,
                    AttemptState.UPLOADING,
                }
            ):
                raise AssignmentConflict(
                    run_id=self.id,
                    assignment_id=existing.assignment_id,
                    reason="start_commit_superseded",
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
        _require_utc_command_time("observed_at", observed_at)
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
        if not (self.assignment.claimed_at <= observed_at < self.assignment.expires_at):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=assignment_id,
                reason=(
                    "assignment_expired"
                    if observed_at >= self.assignment.expires_at
                    else "commit_before_claim"
                ),
            )
        if any(attempt.id == new_attempt_id for attempt in self.attempts):
            raise AttemptConflict(
                run_id=self.id,
                attempt_id=new_attempt_id,
                reason="attempt_id_reused",
            )
        retry_provenance = None
        pending_retry = self.pending_retry_intent
        if self.attempts:
            if pending_retry is None or self.assignment.retry_intent_id != pending_retry.id:
                raise RetryNotAllowed(
                    run_id=self.id,
                    attempt_id=self.attempts[-1].id,
                    adjudication_id=(
                        pending_retry.adjudication_id if pending_retry is not None else ""
                    ),
                    reason="retry_intent_not_pending",
                )
            if (
                spec_digest != pending_retry.execution_spec_digest
                or self.assignment.spec_digest != pending_retry.execution_spec_digest
            ):
                raise RetryNotAllowed(
                    run_id=self.id,
                    attempt_id=pending_retry.source_attempt_id,
                    adjudication_id=pending_retry.adjudication_id,
                    reason="retry_spec_mismatch",
                )
            source = self._current_attempt(pending_retry.source_attempt_id)
            self._ensure_retry_intent_authoritative(
                retry_intent=pending_retry,
                source=source,
            )
            retry_provenance = RetryProvenance.from_intent(pending_retry)
        elif pending_retry is not None or self.assignment.retry_intent_id is not None:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=pending_retry.source_attempt_id if pending_retry is not None else "",
                adjudication_id=(
                    pending_retry.adjudication_id if pending_retry is not None else ""
                ),
                reason="retry_intent_not_pending",
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
            retry_provenance=retry_provenance,
        )
        committed_assignment = self.assignment.commit(committed_at=observed_at)
        committed_run = replace(
            self,
            state=RunState.RUNNING,
            version=self.version + 1,
            current_fence=fence,
            assignments=self._replace_current_assignment(committed_assignment),
            attempts=(*self.attempts, attempt),
            pending_retry_intent_id=None,
        )
        return CommitStartResult(
            run=committed_run,
            attempt=attempt,
            fence=fence,
            replayed=False,
        )

    def expire_precommit_assignment(
        self,
        *,
        assignment_id: str,
        expiry_key: str,
        observed_at: datetime,
        expected_version: int,
    ) -> Run:
        """Close an uncommitted reservation after its frozen TTL boundary."""
        _require_utc_command_time("observed_at", observed_at)
        existing = self._assignment_by_id(assignment_id)
        closure = AssignmentClosure(
            assignment_id=assignment_id,
            idempotency_key=expiry_key,
            kind=AssignmentClosureKind.EXPIRED_PRESTART,
            effective_at=(
                min(observed_at, existing.expires_at) if existing is not None else observed_at
            ),
            recorded_at=observed_at,
            worker=None,
        )
        return self._close_precommit_assignment(
            closure=closure,
            expected_version=expected_version,
        )

    def release_precommit_assignment(
        self,
        *,
        assignment_id: str,
        worker: WorkerRef,
        release_key: str,
        observed_at: datetime,
        expected_version: int,
    ) -> Run:
        """Let the bound Worker release an offer before its TTL boundary."""
        _require_utc_command_time("observed_at", observed_at)
        closure = AssignmentClosure(
            assignment_id=assignment_id,
            idempotency_key=release_key,
            kind=AssignmentClosureKind.RELEASED_PRESTART,
            effective_at=observed_at,
            recorded_at=observed_at,
            worker=worker,
        )
        return self._close_precommit_assignment(
            closure=closure,
            expected_version=expected_version,
        )

    def _close_precommit_assignment(
        self,
        *,
        closure: AssignmentClosure,
        expected_version: int,
    ) -> Run:
        existing = self._assignment_by_id(closure.assignment_id)
        if existing is not None and existing.closure is not None:
            if existing.closure.idempotency_key == closure.idempotency_key:
                if existing.closure.request_digest != closure.request_digest:
                    raise IdempotencyConflict(
                        scope=(
                            f"run:{self.id}:assignment:{closure.assignment_id}:precommit-closure"
                        ),
                        key=closure.idempotency_key,
                        stored_digest=existing.closure.request_digest,
                        received_digest=closure.request_digest,
                    )
                return self
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=closure.assignment_id,
                reason="assignment_already_closed",
            )
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        current = self.assignment
        if (
            self.state is not RunState.ASSIGNED
            or current is None
            or current.id != closure.assignment_id
            or current.state not in {AssignmentState.OFFERED, AssignmentState.CLAIMED}
        ):
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=closure.assignment_id,
                reason="assignment_not_precommit",
            )
        if closure.kind is AssignmentClosureKind.EXPIRED_PRESTART:
            if closure.recorded_at < current.expires_at:
                raise AssignmentConflict(
                    run_id=self.id,
                    assignment_id=closure.assignment_id,
                    reason="assignment_not_expired",
                )
        else:
            if closure.worker != current.worker:
                raise AssignmentConflict(
                    run_id=self.id,
                    assignment_id=closure.assignment_id,
                    reason="worker_mismatch",
                )
            release_floor = current.claimed_at or current.offered_at
            if closure.recorded_at < release_floor:
                raise AssignmentConflict(
                    run_id=self.id,
                    assignment_id=closure.assignment_id,
                    reason=(
                        "release_before_claim"
                        if current.claimed_at is not None
                        else "assignment_not_effective"
                    ),
                )
            if closure.recorded_at >= current.expires_at:
                raise AssignmentConflict(
                    run_id=self.id,
                    assignment_id=closure.assignment_id,
                    reason="assignment_expired",
                )
        closed = current.close_prestart(closure)
        resume_state = (
            RunState.RETRY_QUEUED if current.retry_intent_id is not None else RunState.QUEUED
        )
        return replace(
            self,
            state=resume_state,
            assignments=self._replace_current_assignment(closed),
            current_assignment_id=None,
            version=self.version + 1,
        )

    def mark_current_attempt_unknown(
        self,
        *,
        attempt_id: str,
        observation: UnknownObservation,
        expected_version: int,
        expected_attempt_version: int,
    ) -> Run:
        """Record unknown on the Run-owned latest Attempt snapshot."""
        current = self._current_attempt(attempt_id)
        authority = AttemptAuthority(
            current_fence=self.current_fence,
            current_worker=current.worker,
        )
        unknown = current.mark_unknown(
            observation=observation,
            authority=authority,
            expected_version=expected_attempt_version,
        )
        if unknown is current:
            return self
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        return replace(
            self,
            attempts=(*self.attempts[:-1], unknown),
            version=self.version + 1,
        )

    def queue_adjudicated_retry(
        self,
        *,
        retry_intent: RetryIntent,
        expected_version: int,
    ) -> Run:
        """Persist one chain-tail adjudication as a non-executing retry intent."""
        existing = next(
            (intent for intent in self.retry_intents if intent.id == retry_intent.id),
            None,
        )
        if existing is not None:
            if existing.digest != retry_intent.digest:
                raise IdempotencyConflict(
                    scope=f"run:{self.id}:retry-intent",
                    key=retry_intent.id,
                    stored_digest=existing.digest,
                    received_digest=retry_intent.digest,
                )
            return self
        source = self._current_attempt(retry_intent.source_attempt_id)
        self._ensure_retry_intent_authoritative(
            retry_intent=retry_intent,
            source=source,
        )
        if self.state is not RunState.RUNNING or self.pending_retry_intent is not None:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=retry_intent.adjudication_id,
                reason="retry_already_queued",
            )
        if self.assignment is None or self.assignment.state is not AssignmentState.COMMITTED:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=retry_intent.adjudication_id,
                reason="source_assignment_not_committed",
            )
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        return replace(
            self,
            state=RunState.RETRY_QUEUED,
            version=self.version + 1,
            current_assignment_id=None,
            retry_intents=(*self.retry_intents, retry_intent),
            pending_retry_intent_id=retry_intent.id,
        )

    def ensure_automatic_retry_source_is_not_unknown(
        self, *, attempt_id: str, expected_version: int
    ) -> None:
        """Let later retry policy proceed only when its source is not unknown."""
        current = self._current_attempt(attempt_id)
        if current.state is AttemptState.ATTEMPT_UNKNOWN:
            raise AttemptUnknownReviewRequired(
                run_id=self.id,
                attempt_id=attempt_id,
                fence=self.current_fence,
                reason="manual_adjudication_required",
            )
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )

    def append_current_unknown_adjudication(
        self,
        *,
        attempt_id: str,
        adjudication: UnknownAdjudication,
        expected_version: int,
        expected_attempt_version: int,
    ) -> Run:
        """Append adjudication to the Run-owned latest unknown snapshot."""
        current = self._current_attempt(attempt_id)
        adjudicated = current.append_unknown_adjudication(
            adjudication=adjudication,
            expected_version=expected_attempt_version,
        )
        if adjudicated is current:
            return self
        if self.pending_retry_intent is not None or self.state is not RunState.RUNNING:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=attempt_id,
                adjudication_id=adjudication.id,
                reason="retry_already_queued",
            )
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        return replace(
            self,
            attempts=(*self.attempts[:-1], adjudicated),
            version=self.version + 1,
        )

    def _replace_current_assignment(self, updated: Assignment) -> tuple[Assignment, ...]:
        if self.current_assignment_id is None or updated.id != self.current_assignment_id:
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=updated.id,
                reason="current_assignment_missing",
            )
        replaced = tuple(
            updated if assignment.id == self.current_assignment_id else assignment
            for assignment in self.assignments
        )
        if replaced == self.assignments:
            raise AssignmentConflict(
                run_id=self.id,
                assignment_id=updated.id,
                reason="current_assignment_missing",
            )
        return replaced

    def _assignment_by_id(self, assignment_id: str) -> Assignment | None:
        return next(
            (assignment for assignment in self.assignments if assignment.id == assignment_id),
            None,
        )

    def _ensure_retry_intent_authoritative(
        self,
        *,
        retry_intent: RetryIntent,
        source: Attempt,
    ) -> None:
        if source.state is not AttemptState.ATTEMPT_UNKNOWN:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=retry_intent.adjudication_id,
                reason="source_attempt_not_unknown",
            )
        if not source.adjudications:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=retry_intent.adjudication_id,
                reason="adjudication_missing",
            )
        current_adjudication = source.adjudications[-1]
        if current_adjudication.id != retry_intent.adjudication_id:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=retry_intent.adjudication_id,
                reason="adjudication_not_current",
            )
        if not current_adjudication.decision.permits_retry:
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=current_adjudication.id,
                reason="decision_does_not_permit_retry",
            )
        if (
            retry_intent.run_id != self.id
            or retry_intent.source_attempt_no != source.attempt_no
            or retry_intent.source_fence != source.fence
            or retry_intent.adjudication_digest != current_adjudication.digest
            or retry_intent.decision is not current_adjudication.decision
            or retry_intent.execution_spec_digest != source.spec_digest
            or retry_intent.created_at < current_adjudication.occurred_at
        ):
            raise RetryNotAllowed(
                run_id=self.id,
                attempt_id=source.id,
                adjudication_id=current_adjudication.id,
                reason="retry_intent_mismatch",
            )

    def _current_attempt(self, attempt_id: str) -> Attempt:
        if (
            not self.attempts
            or self.attempts[-1].id != attempt_id
            or self.attempts[-1].run_id != self.id
        ):
            raise AttemptUnknownReviewRequired(
                run_id=self.id,
                attempt_id=attempt_id,
                fence=self.current_fence,
                reason="source_attempt_not_current",
            )
        current = self.attempts[-1]
        if current.fence != self.current_fence:
            raise StaleFence(
                attempt_id=current.id,
                current_fence=self.current_fence,
                received_fence=current.fence,
            )
        return current


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


def _invalid_run(field: str, reason: str) -> None:
    raise DomainValidationError(
        entity_type="run",
        field=field,
        reason=reason,
    )


def _require_utc_command_time(field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid_run(field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid_run(field, "not_utc")
