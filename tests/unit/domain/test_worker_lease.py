"""T-M3-RENEW-001 / T-M3-GEN-001 (domain half): lease renew + generation fence.

Observable contract from WORKER_PROTOCOL §5.5 and T-M3-RENEW/GEN:
- renew accepts only a higher lease_version;
- expired leases cannot be extended (late response cannot revive);
- stop commands freeze further continue renewals;
- generation rotation rejects requests from a superseded WorkerRef.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def _worker(generation: int = 1):
    from qarunner.domain import WorkerRef

    return WorkerRef(worker_id="worker-001", generation=generation)


def test_first_lease_after_commit_start() -> None:
    from qarunner.domain import AssignmentLease, LeaseCommand

    lease = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=30),
    )
    assert lease.lease_version == 1
    assert lease.command is LeaseCommand.CONTINUE
    assert lease.expires_at == T0 + timedelta(seconds=30)
    assert lease.worker.generation == 1


def test_renew_requires_strictly_higher_version() -> None:
    from qarunner.domain import AssignmentLease, LeaseCommand, WorkerLeaseConflict

    lease = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=30),
    )
    renewed = lease.renew(
        request_lease_version=1,
        observed_at=T0 + timedelta(seconds=5),
        ttl=timedelta(seconds=30),
        command=LeaseCommand.CONTINUE,
    )
    assert renewed.lease_version == 2
    assert renewed.expires_at == T0 + timedelta(seconds=5) + timedelta(seconds=30)

    with pytest.raises(WorkerLeaseConflict) as caught:
        renewed.renew(
            request_lease_version=1,
            observed_at=T0 + timedelta(seconds=6),
            ttl=timedelta(seconds=30),
            command=LeaseCommand.CONTINUE,
        )
    assert caught.value.reason == "stale_lease_version"


def test_expired_lease_cannot_be_renewed() -> None:
    from qarunner.domain import AssignmentLease, LeaseCommand, WorkerLeaseConflict

    lease = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=10),
    )
    with pytest.raises(WorkerLeaseConflict) as caught:
        lease.renew(
            request_lease_version=1,
            observed_at=T0 + timedelta(seconds=11),
            ttl=timedelta(seconds=30),
            command=LeaseCommand.CONTINUE,
        )
    assert caught.value.reason == "lease_expired"


def test_stop_command_blocks_continue_renewal() -> None:
    from qarunner.domain import AssignmentLease, LeaseCommand, WorkerLeaseConflict

    lease = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=30),
        command=LeaseCommand.STOP_CANCELLED,
    )
    with pytest.raises(WorkerLeaseConflict) as caught:
        lease.renew(
            request_lease_version=1,
            observed_at=T0 + timedelta(seconds=1),
            ttl=timedelta(seconds=30),
            command=LeaseCommand.CONTINUE,
        )
    assert caught.value.reason == "stop_command_active"


def test_generation_mismatch_rejects_renew() -> None:
    from qarunner.domain import AssignmentLease, LeaseCommand, WorkerGenerationConflict

    lease = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(generation=1),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=30),
    )
    with pytest.raises(WorkerGenerationConflict):
        lease.renew(
            request_lease_version=1,
            observed_at=T0 + timedelta(seconds=1),
            ttl=timedelta(seconds=30),
            command=LeaseCommand.CONTINUE,
            worker=_worker(generation=2),
        )


def test_issue_rejects_invalid_ttl_and_identity() -> None:
    from qarunner.domain import AssignmentLease, DomainValidationError, WorkerRef

    with pytest.raises(DomainValidationError):
        AssignmentLease.issue(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=1,
            lease_version=1,
            issued_at=T0,
            ttl=timedelta(0),
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="",
            worker=_worker(),
            fence=1,
            lease_version=1,
            issued_at=T0,
            expires_at=T0 + timedelta(seconds=1),
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="assignment-001",
            worker=object(),  # type: ignore[arg-type]
            fence=1,
            lease_version=1,
            issued_at=T0,
            expires_at=T0 + timedelta(seconds=1),
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=0,
            lease_version=1,
            issued_at=T0,
            expires_at=T0 + timedelta(seconds=1),
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=1,
            lease_version=0,
            issued_at=T0,
            expires_at=T0 + timedelta(seconds=1),
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=1,
            lease_version=1,
            issued_at=T0.replace(tzinfo=None),
            expires_at=T0 + timedelta(seconds=1),
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=1,
            lease_version=1,
            issued_at=T0,
            expires_at=T0,
        )
    with pytest.raises(DomainValidationError):
        AssignmentLease(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=1,
            lease_version=1,
            issued_at=T0,
            expires_at=T0 + timedelta(seconds=1),
            command="nope",  # type: ignore[arg-type]
        )
    del WorkerRef


def test_renew_rejects_bad_observed_at_ttl_and_command() -> None:
    from qarunner.domain import AssignmentLease, DomainValidationError, LeaseCommand

    lease = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=30),
    )
    with pytest.raises(DomainValidationError):
        lease.renew(
            request_lease_version=1,
            observed_at=T0.replace(tzinfo=None),
            ttl=timedelta(seconds=30),
        )
    with pytest.raises(DomainValidationError):
        lease.renew(
            request_lease_version=1,
            observed_at=T0 + timedelta(seconds=1),
            ttl=timedelta(0),
        )
    with pytest.raises(DomainValidationError):
        lease.renew(
            request_lease_version=1,
            observed_at=T0 + timedelta(seconds=1),
            ttl=timedelta(seconds=30),
            command="bad",  # type: ignore[arg-type]
        )
    # stop command renew to another stop is allowed (command change after stop)
    stopped = AssignmentLease.issue(
        assignment_id="assignment-001",
        worker=_worker(),
        fence=1,
        lease_version=1,
        issued_at=T0,
        ttl=timedelta(seconds=30),
        command=LeaseCommand.STOP_EXPIRED,
    )
    renewed = stopped.renew(
        request_lease_version=1,
        observed_at=T0 + timedelta(seconds=1),
        ttl=timedelta(seconds=30),
        command=LeaseCommand.QUARANTINE,
    )
    assert renewed.command is LeaseCommand.QUARANTINE


def test_issued_at_must_be_datetime() -> None:
    from qarunner.domain import AssignmentLease, DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        AssignmentLease(
            assignment_id="assignment-001",
            worker=_worker(),
            fence=1,
            lease_version=1,
            issued_at="now",  # type: ignore[arg-type]
            expires_at=T0 + timedelta(seconds=1),
        )
    assert caught.value.field == "issued_at"
