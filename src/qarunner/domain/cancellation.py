"""Immutable cancellation intent owned by a Run."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.worker import WorkerRef


class CancellationSource(enum.StrEnum):
    """Why the control plane recorded cancellation intent."""

    USER_REQUEST = "user_request"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    POLICY_ENFORCEMENT = "policy_enforcement"


@dataclass(frozen=True, slots=True)
class CancellationIntent:
    """A request to stop work, distinct from a proven cancellation outcome."""

    run_id: str
    idempotency_key: str
    source: CancellationSource
    actor_id: str
    reason: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field in ("run_id", "idempotency_key", "actor_id", "reason"):
            _require_nonempty_string("cancellation_intent", field, getattr(self, field))
        if not isinstance(self.source, CancellationSource):
            _invalid("cancellation_intent", "source", "unknown")
        _require_utc("cancellation_intent", "recorded_at", self.recorded_at)

    @property
    def request_digest(self) -> Digest:
        """Bind replay identity to caller-controlled cancellation content."""
        return canonical_digest(
            schema_version="qep.cancellation-request.v1",
            payload={
                "run_id": self.run_id,
                "idempotency_key": self.idempotency_key,
                "source": self.source.value,
                "actor_id": self.actor_id,
                "reason": self.reason,
            },
        )

    @property
    def digest(self) -> Digest:
        """Digest the immutable cancellation fact including server record time."""
        return canonical_digest(
            schema_version="qep.cancellation-intent.v1",
            payload={
                "request_digest": self.request_digest.value,
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class TrustedCancellationStop:
    """Control-plane proof that execution and approved SUT access both stopped."""

    run_id: str
    attempt_id: str
    fence: int
    worker: WorkerRef
    cancellation_intent_digest: Digest
    source_event_id: str
    process_stopped_at: datetime
    sut_access_stopped_at: datetime
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field in ("run_id", "attempt_id", "source_event_id"):
            _require_nonempty_string("trusted_cancellation_stop", field, getattr(self, field))
        if isinstance(self.fence, bool) or not isinstance(self.fence, int):
            _invalid("trusted_cancellation_stop", "fence", "not_integer")
        if self.fence < 1:
            _invalid("trusted_cancellation_stop", "fence", "not_positive")
        if not isinstance(self.worker, WorkerRef):
            _invalid("trusted_cancellation_stop", "worker", "invalid_type")
        if not isinstance(self.cancellation_intent_digest, Digest):
            _invalid(
                "trusted_cancellation_stop",
                "cancellation_intent_digest",
                "not_digest",
            )
        for field in ("process_stopped_at", "sut_access_stopped_at", "recorded_at"):
            _require_utc("trusted_cancellation_stop", field, getattr(self, field))
        if self.recorded_at < max(self.process_stopped_at, self.sut_access_stopped_at):
            _invalid(
                "trusted_cancellation_stop",
                "recorded_at",
                "before_stop_facts",
            )

    @property
    def digest(self) -> Digest:
        """Bind the complete cancellation convergence proof."""
        return canonical_digest(
            schema_version="qep.trusted-cancellation-stop.v1",
            payload={
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "fence": self.fence,
                "worker": {
                    "worker_id": self.worker.worker_id,
                    "generation": self.worker.generation,
                },
                "cancellation_intent_digest": self.cancellation_intent_digest.value,
                "source_event_id": self.source_event_id,
                "process_stopped_at": self.process_stopped_at.isoformat().replace("+00:00", "Z"),
                "sut_access_stopped_at": self.sut_access_stopped_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity_type, field, "not_utc")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity_type, field=field, reason=reason)
