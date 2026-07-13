"""Assignment reservation bound to one Worker generation and execution spec."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.worker import WorkerRef


class AssignmentState(enum.StrEnum):
    """Pre-execution states for one short-lived Assignment."""

    OFFERED = "offered"
    CLAIMED = "claimed"
    COMMITTED = "committed"
    EXPIRED_PRESTART = "expired_prestart"
    RELEASED_PRESTART = "released_prestart"


class AssignmentClosureKind(enum.StrEnum):
    """Why a reservation ended before commit-start created an Attempt."""

    EXPIRED_PRESTART = "expired_prestart"
    RELEASED_PRESTART = "released_prestart"


@dataclass(frozen=True, slots=True)
class AssignmentClosure:
    """Immutable, idempotent fact that closes one pre-commit reservation."""

    assignment_id: str
    idempotency_key: str
    kind: AssignmentClosureKind
    effective_at: datetime
    recorded_at: datetime
    worker: WorkerRef | None

    def __post_init__(self) -> None:
        _require_nonempty_string("assignment_closure", "assignment_id", self.assignment_id)
        _require_nonempty_string(
            "assignment_closure",
            "idempotency_key",
            self.idempotency_key,
        )
        if not isinstance(self.kind, AssignmentClosureKind):
            _invalid_entity("assignment_closure", "kind", "unknown")
        _require_utc("assignment_closure", "effective_at", self.effective_at)
        _require_utc("assignment_closure", "recorded_at", self.recorded_at)
        if self.recorded_at < self.effective_at:
            _invalid_entity(
                "assignment_closure",
                "recorded_at",
                "before_effective_at",
            )
        if self.worker is not None and not isinstance(self.worker, WorkerRef):
            _invalid_entity("assignment_closure", "worker", "invalid_type")
        if self.kind is AssignmentClosureKind.EXPIRED_PRESTART:
            if self.worker is not None:
                _invalid_entity(
                    "assignment_closure",
                    "worker",
                    "forbidden_for_kind",
                )
        elif self.worker is None:
            _invalid_entity(
                "assignment_closure",
                "worker",
                "required_for_kind",
            )

    @property
    def request_digest(self) -> Digest:
        """Bind idempotency to caller-controlled content, excluding server time."""
        return canonical_digest(
            schema_version="qep.assignment-closure-request.v1",
            payload={
                "assignment_id": self.assignment_id,
                "idempotency_key": self.idempotency_key,
                "kind": self.kind.value,
                "worker": (
                    None
                    if self.worker is None
                    else {
                        "worker_id": self.worker.worker_id,
                        "generation": self.worker.generation,
                    }
                ),
            },
        )

    @property
    def digest(self) -> Digest:
        """Digest the complete immutable closure fact for audit and rehydration."""
        return canonical_digest(
            schema_version="qep.assignment-closure.v1",
            payload={
                "request_digest": self.request_digest.value,
                "effective_at": self.effective_at.isoformat().replace("+00:00", "Z"),
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class Assignment:
    """Immutable reservation for a Run on a specific Worker generation."""

    id: str
    worker: WorkerRef
    spec_digest: Digest
    state: AssignmentState
    offered_at: datetime
    expires_at: datetime
    claimed_at: datetime | None = None
    committed_at: datetime | None = None
    retry_intent_id: str | None = None
    closure: AssignmentClosure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str):
            _invalid("id", "not_string")
        if not self.id.strip():
            _invalid("id", "empty")
        if not isinstance(self.worker, WorkerRef):
            _invalid("worker", "invalid_type")
        if not isinstance(self.spec_digest, Digest):
            _invalid("spec_digest", "not_digest")
        if not isinstance(self.state, AssignmentState):
            _invalid("state", "unknown")
        _require_utc("assignment", "offered_at", self.offered_at)
        _require_utc("assignment", "expires_at", self.expires_at)
        if self.expires_at <= self.offered_at:
            _invalid("expires_at", "not_after_offered_at")
        for field in ("claimed_at", "committed_at"):
            value = getattr(self, field)
            if value is not None:
                _require_utc("assignment", field, value)
        if self.claimed_at is not None and not (
            self.offered_at <= self.claimed_at < self.expires_at
        ):
            _invalid("claimed_at", "outside_offer_window")
        if self.committed_at is not None and (
            self.claimed_at is None or not (self.claimed_at <= self.committed_at < self.expires_at)
        ):
            _invalid("committed_at", "outside_claim_window")
        if self.retry_intent_id is not None and (
            not isinstance(self.retry_intent_id, str) or not self.retry_intent_id.strip()
        ):
            _invalid("retry_intent_id", "invalid")
        if self.closure is not None and not isinstance(self.closure, AssignmentClosure):
            _invalid("closure", "invalid_type")
        terminal_kind = {
            AssignmentState.EXPIRED_PRESTART: AssignmentClosureKind.EXPIRED_PRESTART,
            AssignmentState.RELEASED_PRESTART: AssignmentClosureKind.RELEASED_PRESTART,
        }.get(self.state)
        if terminal_kind is None:
            if self.closure is not None:
                _invalid("closure", "forbidden_for_state")
            if self.state is AssignmentState.OFFERED and (
                self.claimed_at is not None or self.committed_at is not None
            ):
                _invalid("claimed_at", "forbidden_for_state")
            if self.state is AssignmentState.CLAIMED and (
                self.claimed_at is None or self.committed_at is not None
            ):
                _invalid("claimed_at", "required_for_state")
            if self.state is AssignmentState.COMMITTED and self.committed_at is None:
                _invalid("committed_at", "required_for_state")
            return
        if self.committed_at is not None:
            _invalid("committed_at", "forbidden_for_state")
        if self.closure is None:
            _invalid("closure", "required_for_state")
        if self.closure.assignment_id != self.id:
            _invalid("closure", "assignment_mismatch")
        if self.closure.kind is not terminal_kind:
            _invalid("closure", "state_mismatch")
        if self.closure.kind is AssignmentClosureKind.EXPIRED_PRESTART:
            if self.closure.effective_at != self.expires_at:
                _invalid("closure", "expiry_effective_at_mismatch")
        else:
            if self.closure.worker != self.worker:
                _invalid("closure", "worker_mismatch")
            if self.claimed_at is not None and self.closure.effective_at < self.claimed_at:
                _invalid("closure", "before_claimed_at")
            if self.closure.effective_at < self.offered_at:
                _invalid("closure", "before_offered_at")
            if self.closure.effective_at >= self.expires_at:
                _invalid("closure", "at_or_after_expiry")

    @classmethod
    def offer(
        cls,
        *,
        assignment_id: str,
        worker: WorkerRef,
        spec_digest: Digest,
        offered_at: datetime,
        expires_at: datetime,
        retry_intent_id: str | None = None,
    ) -> Assignment:
        """Create an offered Assignment."""
        return cls(
            id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
            state=AssignmentState.OFFERED,
            offered_at=offered_at,
            expires_at=expires_at,
            retry_intent_id=retry_intent_id,
        )

    def claim(self, *, claimed_at: datetime) -> Assignment:
        """Accept the offer for its bound Worker generation."""
        if self.state is not AssignmentState.OFFERED:
            _invalid("state", "transition_not_allowed")
        return replace(self, state=AssignmentState.CLAIMED, claimed_at=claimed_at)

    def commit(self, *, committed_at: datetime) -> Assignment:
        """Bind the claimed Assignment to a durable Attempt."""
        if self.state is not AssignmentState.CLAIMED:
            _invalid("state", "transition_not_allowed")
        return replace(self, state=AssignmentState.COMMITTED, committed_at=committed_at)

    def close_prestart(self, closure: AssignmentClosure) -> Assignment:
        """End an offered/claimed reservation without creating an Attempt."""
        if self.state not in {AssignmentState.OFFERED, AssignmentState.CLAIMED}:
            _invalid("state", "transition_not_allowed")
        state = {
            AssignmentClosureKind.EXPIRED_PRESTART: AssignmentState.EXPIRED_PRESTART,
            AssignmentClosureKind.RELEASED_PRESTART: AssignmentState.RELEASED_PRESTART,
        }[closure.kind]
        return replace(self, state=state, closure=closure)


def _invalid(field: str, reason: str) -> None:
    _invalid_entity("assignment", field, reason)


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid_entity(entity_type, field, "not_string")
    if not value.strip():
        _invalid_entity(entity_type, field, "empty")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid_entity(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid_entity(entity_type, field, "not_utc")


def _invalid_entity(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity_type, field=field, reason=reason)
