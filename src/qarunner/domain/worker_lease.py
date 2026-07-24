"""Assignment lease renew semantics for the Worker protocol (T-M3-RENEW-001).

Control-plane lease facts only: no Docker, no network. Fake Worker and real
Worker agents both consume these immutable lease values.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from qarunner.domain.errors import (
    DomainValidationError,
    WorkerGenerationConflict,
    WorkerLeaseConflict,
)
from qarunner.domain.worker import WorkerRef


class LeaseCommand(enum.StrEnum):
    """Control-plane instruction returned on renew (WORKER_PROTOCOL §5.5)."""

    CONTINUE = "continue"
    STOP_CANCELLED = "stop_cancelled"
    STOP_EXPIRED = "stop_expired"
    STOP_POLICY_REVOKED = "stop_policy_revoked"
    DRAIN_AFTER_CURRENT = "drain_after_current"
    QUARANTINE = "quarantine"


_STOP_COMMANDS = frozenset(
    {
        LeaseCommand.STOP_CANCELLED,
        LeaseCommand.STOP_EXPIRED,
        LeaseCommand.STOP_POLICY_REVOKED,
        LeaseCommand.QUARANTINE,
    }
)


@dataclass(frozen=True, slots=True)
class AssignmentLease:
    """Immutable lease snapshot bound to one Assignment fence and Worker generation."""

    assignment_id: str
    worker: WorkerRef
    fence: int
    lease_version: int
    issued_at: datetime
    expires_at: datetime
    command: LeaseCommand = LeaseCommand.CONTINUE

    def __post_init__(self) -> None:
        if not isinstance(self.assignment_id, str) or not self.assignment_id.strip():
            raise DomainValidationError(
                entity_type="assignment_lease", field="assignment_id", reason="invalid"
            )
        if not isinstance(self.worker, WorkerRef):
            raise DomainValidationError(
                entity_type="assignment_lease", field="worker", reason="not_worker_ref"
            )
        if isinstance(self.fence, bool) or not isinstance(self.fence, int) or self.fence < 1:
            raise DomainValidationError(
                entity_type="assignment_lease", field="fence", reason="invalid"
            )
        if (
            isinstance(self.lease_version, bool)
            or not isinstance(self.lease_version, int)
            or self.lease_version < 1
        ):
            raise DomainValidationError(
                entity_type="assignment_lease", field="lease_version", reason="invalid"
            )
        for field in ("issued_at", "expires_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime):
                raise DomainValidationError(
                    entity_type="assignment_lease", field=field, reason="not_datetime"
                )
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise DomainValidationError(
                    entity_type="assignment_lease", field=field, reason="not_utc"
                )
        if self.expires_at <= self.issued_at:
            raise DomainValidationError(
                entity_type="assignment_lease",
                field="expires_at",
                reason="not_after_issued_at",
            )
        if not isinstance(self.command, LeaseCommand):
            raise DomainValidationError(
                entity_type="assignment_lease", field="command", reason="unknown"
            )

    @classmethod
    def issue(
        cls,
        *,
        assignment_id: str,
        worker: WorkerRef,
        fence: int,
        lease_version: int,
        issued_at: datetime,
        ttl: timedelta,
        command: LeaseCommand = LeaseCommand.CONTINUE,
    ) -> AssignmentLease:
        """Issue the first (or control-plane-reset) lease for a committed assignment."""
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise DomainValidationError(
                entity_type="assignment_lease", field="ttl", reason="invalid"
            )
        return cls(
            assignment_id=assignment_id,
            worker=worker,
            fence=fence,
            lease_version=lease_version,
            issued_at=issued_at,
            expires_at=issued_at + ttl,
            command=command,
        )

    def renew(
        self,
        *,
        request_lease_version: int,
        observed_at: datetime,
        ttl: timedelta,
        command: LeaseCommand = LeaseCommand.CONTINUE,
        worker: WorkerRef | None = None,
    ) -> AssignmentLease:
        """Extend the lease when the request carries the current version and is in-window.

        `request_lease_version` is the version the Worker last observed (must equal the
        current lease_version). Successful renew returns version+1.
        """
        requesting = self.worker if worker is None else worker
        if (
            requesting.worker_id != self.worker.worker_id
            or requesting.generation != self.worker.generation
        ):
            raise WorkerGenerationConflict(
                current_worker_id=self.worker.worker_id,
                current_generation=self.worker.generation,
                received_worker_id=requesting.worker_id,
                received_generation=requesting.generation,
                reason="generation_mismatch",
            )
        if not isinstance(observed_at, datetime) or (
            observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0)
        ):
            raise DomainValidationError(
                entity_type="assignment_lease", field="observed_at", reason="not_utc"
            )
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise DomainValidationError(
                entity_type="assignment_lease", field="ttl", reason="invalid"
            )
        if (
            isinstance(request_lease_version, bool)
            or not isinstance(request_lease_version, int)
            or request_lease_version != self.lease_version
        ):
            raise WorkerLeaseConflict(
                assignment_id=self.assignment_id, reason="stale_lease_version"
            )
        if observed_at >= self.expires_at:
            raise WorkerLeaseConflict(assignment_id=self.assignment_id, reason="lease_expired")
        if self.command in _STOP_COMMANDS and command is LeaseCommand.CONTINUE:
            raise WorkerLeaseConflict(
                assignment_id=self.assignment_id, reason="stop_command_active"
            )
        if not isinstance(command, LeaseCommand):
            raise DomainValidationError(
                entity_type="assignment_lease", field="command", reason="unknown"
            )
        return replace(
            self,
            lease_version=self.lease_version + 1,
            issued_at=observed_at,
            expires_at=observed_at + ttl,
            command=command,
        )
