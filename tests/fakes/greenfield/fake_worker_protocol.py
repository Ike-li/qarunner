"""Deterministic Fake Worker protocol session (M3).

Composes existing Run/Assignment/Attempt/lease domain contracts into an
in-memory Worker-facing session. No container runtime, no network, no real mTLS.

This is not a real Worker agent: it proves protocol-side claim / commit-start /
renew / event / generation rules under controlled clocks and delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain import (
    AssignmentLease,
    AttemptAuthority,
    AttemptEvent,
    Digest,
    LeaseCommand,
    Run,
    RunState,
    WorkerAuthority,
    WorkerGeneration,
    WorkerLeaseConflict,
    WorkerRef,
    canonical_digest,
)
from qarunner.domain.run import CommitStartResult


@dataclass(frozen=True, slots=True)
class EventAppendResult:
    run: Run
    attempt_version: int
    replayed: bool


class FakeWorkerProtocolSession:
    """In-memory control-plane + Fake Worker conversation for one generation."""

    def __init__(
        self,
        *,
        worker: WorkerGeneration,
        worker_authority: WorkerAuthority,
        clock_start: datetime,
    ) -> None:
        self._worker = worker
        self._worker_authority = worker_authority
        self._now = clock_start
        self._runs: dict[str, Run] = {}
        self._leases: dict[str, AssignmentLease] = {}

    @property
    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta

    def rotate_generation(
        self, *, worker: WorkerGeneration, worker_authority: WorkerAuthority
    ) -> None:
        """Activate a new Worker generation; old refs must fail claim/renew."""
        self._worker = worker
        self._worker_authority = worker_authority

    def seed_offered_run(
        self,
        *,
        run_id: str,
        assignment_id: str,
        spec_digest: Digest,
        expires_at: datetime,
    ) -> Run:
        planned = Run.create(run_id=run_id)
        queued = planned.transition(RunState.QUEUED, expected_version=0)
        offered = queued.offer_assignment(
            assignment_id=assignment_id,
            worker=self._worker,
            worker_authority=self._worker_authority,
            spec_digest=spec_digest,
            offered_at=self._now,
            expires_at=expires_at,
            expected_version=1,
        )
        self._runs[run_id] = offered
        return offered

    def claim(self, *, run_id: str, assignment_id: str) -> Run:
        # claimable_ref raises WorkerNotClaimable when generation is not READY/BUSY.
        self._worker.claimable_ref(authority=self._worker_authority)
        run = self._require_run(run_id)
        claimed = run.claim_assignment(
            assignment_id=assignment_id,
            worker=self._worker.ref,
            observed_at=self._now,
            expected_version=run.version,
        )
        self._runs[run_id] = claimed
        return claimed

    def commit_start(
        self,
        *,
        run_id: str,
        assignment_id: str,
        start_commit_key: str,
        new_attempt_id: str,
    ) -> CommitStartResult:
        run = self._require_run(run_id)
        if run.assignment is None:
            raise WorkerLeaseConflict(assignment_id=assignment_id, reason="no_assignment")
        result = run.commit_start(
            assignment_id=assignment_id,
            worker=self._worker.ref,
            start_commit_key=start_commit_key,
            spec_digest=run.assignment.spec_digest,
            new_attempt_id=new_attempt_id,
            observed_at=self._now,
            expected_version=run.version,
        )
        self._runs[run_id] = result.run
        if not result.replayed:
            self._leases[assignment_id] = AssignmentLease.issue(
                assignment_id=assignment_id,
                worker=self._worker.ref,
                fence=result.fence,
                lease_version=1,
                issued_at=self._now,
                ttl=timedelta(seconds=30),
            )
        return result

    def renew(
        self,
        *,
        assignment_id: str,
        request_lease_version: int,
        ttl: timedelta,
        command: LeaseCommand = LeaseCommand.CONTINUE,
        worker_override: WorkerRef | None = None,
    ) -> AssignmentLease:
        lease = self._leases.get(assignment_id)
        if lease is None:
            raise WorkerLeaseConflict(assignment_id=assignment_id, reason="lease_missing")
        renewed = lease.renew(
            request_lease_version=request_lease_version,
            observed_at=self._now,
            ttl=ttl,
            command=command,
            worker=worker_override if worker_override is not None else self._worker.ref,
        )
        self._leases[assignment_id] = renewed
        return renewed

    def record_event(
        self,
        *,
        run_id: str,
        attempt_id: str,
        event_id: str,
        event_seq: int,
        event_type: str,
        payload_label: str,
    ) -> EventAppendResult:
        run = self._require_run(run_id)
        attempt = next(item for item in run.attempts if item.id == attempt_id)
        event = AttemptEvent(
            event_id=event_id,
            event_seq=event_seq,
            event_type=event_type,
            payload_digest=canonical_digest(
                schema_version="qep.attempt-event-payload.v1",
                payload={"label": payload_label},
            ),
        )
        authority = AttemptAuthority(
            current_fence=run.current_fence,
            current_worker=self._worker.ref,
        )
        before = attempt
        updated_run = run.record_current_attempt_event(
            attempt_id=attempt_id,
            event=event,
            authority=authority,
            worker=self._worker.ref,
            fence=run.current_fence,
            expected_version=run.version,
            expected_attempt_version=attempt.version,
        )
        self._runs[run_id] = updated_run
        updated_attempt = next(item for item in updated_run.attempts if item.id == attempt_id)
        return EventAppendResult(
            run=updated_run,
            attempt_version=updated_attempt.version,
            replayed=updated_attempt is before or updated_attempt.version == before.version,
        )

    def _require_run(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run
