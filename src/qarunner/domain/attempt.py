"""Attempt aggregate for the greenfield execution lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.digest import Digest
from qarunner.domain.errors import (
    EventConflict,
    InvalidTransition,
    StaleFence,
    StaleGeneration,
    ensure_expected_version,
)
from qarunner.domain.event import AttemptEvent
from qarunner.domain.worker import WorkerRef


class AttemptState(enum.StrEnum):
    """States of one real execution Attempt."""

    START_COMMITTED = "start_committed"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    UPLOADING = "uploading"
    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"
    ATTEMPT_UNKNOWN = "attempt_unknown"


_TERMINAL_STATES = frozenset(
    {
        AttemptState.PASSED,
        AttemptState.TEST_FAILED,
        AttemptState.INFRA_FAILED,
        AttemptState.CANCELLED,
        AttemptState.ATTEMPT_UNKNOWN,
    }
)
_ALLOWED_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.START_COMMITTED: frozenset(
        {AttemptState.PROVISIONING, AttemptState.ATTEMPT_UNKNOWN}
    ),
    AttemptState.PROVISIONING: frozenset(
        {AttemptState.RUNNING, AttemptState.UPLOADING, AttemptState.ATTEMPT_UNKNOWN}
    ),
    AttemptState.RUNNING: frozenset({AttemptState.UPLOADING, AttemptState.ATTEMPT_UNKNOWN}),
    # Terminal classification is deliberately not exposed through transition().
    # A later M0 slice adds Evidence finalize with trusted exit facts.
    AttemptState.UPLOADING: frozenset(),
    **{state: frozenset() for state in _TERMINAL_STATES},
}


@dataclass(frozen=True, slots=True)
class Attempt:
    """Immutable record of one committed execution attempt."""

    id: str
    run_id: str
    attempt_no: int
    fence: int
    assignment_id: str
    worker: WorkerRef
    spec_digest: Digest
    start_commit_key: str
    events: tuple[AttemptEvent, ...]
    state: AttemptState
    version: int

    @classmethod
    def create(
        cls,
        *,
        attempt_id: str,
        run_id: str,
        attempt_no: int,
        fence: int,
        assignment_id: str,
        worker: WorkerRef,
        spec_digest: Digest,
        start_commit_key: str,
    ) -> Attempt:
        """Create the Attempt only after start commit is durable."""
        return cls(
            id=attempt_id,
            run_id=run_id,
            attempt_no=attempt_no,
            fence=fence,
            assignment_id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
            start_commit_key=start_commit_key,
            events=(),
            state=AttemptState.START_COMMITTED,
            version=0,
        )

    def transition(self, target: AttemptState, *, expected_version: int) -> Attempt:
        """Reject stale commands or transitions that skip execution phases."""
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="attempt",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(self, state=target, version=self.version + 1)

    def record_event(
        self,
        event: AttemptEvent,
        *,
        worker: WorkerRef,
        fence: int,
        expected_version: int,
    ) -> Attempt:
        """Append a Worker event only for the Attempt's current fence."""
        if worker != self.worker:
            raise StaleGeneration(
                attempt_id=self.id,
                current_worker=self.worker,
                received_worker=worker,
            )
        if fence != self.fence:
            raise StaleFence(
                attempt_id=self.id,
                current_fence=self.fence,
                received_fence=fence,
            )
        existing = next(
            (
                candidate
                for candidate in self.events
                if candidate.event_id == event.event_id or candidate.event_seq == event.event_seq
            ),
            None,
        )
        if existing is not None:
            if existing != event:
                raise EventConflict(
                    attempt_id=self.id,
                    stored_event=existing,
                    received_event=event,
                )
            return self
        ensure_expected_version(
            entity_type="attempt",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        return replace(self, events=(*self.events, event), version=self.version + 1)
