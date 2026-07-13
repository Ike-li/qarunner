"""Immutable retry authorization and Attempt provenance facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.unknown import UnknownAdjudicationDecision


@dataclass(frozen=True, slots=True)
class RetryIntent:
    """Durable Run intent derived from one authoritative adjudication."""

    id: str
    run_id: str
    source_attempt_id: str
    source_attempt_no: int
    source_fence: int
    adjudication_id: str
    adjudication_digest: Digest
    decision: UnknownAdjudicationDecision
    execution_spec_digest: Digest
    created_at: datetime

    def __post_init__(self) -> None:
        for field in ("id", "run_id", "source_attempt_id", "adjudication_id"):
            _require_nonempty_string("retry_intent", field, getattr(self, field))
        _require_positive_integer("retry_intent", "source_attempt_no", self.source_attempt_no)
        _require_positive_integer("retry_intent", "source_fence", self.source_fence)
        _require_digest("retry_intent", "adjudication_digest", self.adjudication_digest)
        if not isinstance(self.decision, UnknownAdjudicationDecision):
            _invalid("retry_intent", "decision", "unknown")
        _require_digest(
            "retry_intent",
            "execution_spec_digest",
            self.execution_spec_digest,
        )
        _require_utc("retry_intent", "created_at", self.created_at)

    @property
    def digest(self) -> Digest:
        """Bind idempotency to the complete retry authorization identity."""
        return canonical_digest(
            schema_version="qep.retry-intent.v1",
            payload={
                "id": self.id,
                "run_id": self.run_id,
                "source_attempt_id": self.source_attempt_id,
                "source_attempt_no": self.source_attempt_no,
                "source_fence": self.source_fence,
                "adjudication_id": self.adjudication_id,
                "adjudication_digest": self.adjudication_digest.value,
                "decision": self.decision.value,
                "execution_spec_digest": self.execution_spec_digest.value,
                "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class RetryProvenance:
    """Frozen authorization lineage copied onto a newly committed Attempt."""

    retry_intent_id: str
    retry_intent_digest: Digest
    source_attempt_id: str
    source_attempt_no: int
    source_fence: int
    adjudication_id: str
    adjudication_digest: Digest
    decision: UnknownAdjudicationDecision

    def __post_init__(self) -> None:
        for field in ("retry_intent_id", "source_attempt_id", "adjudication_id"):
            _require_nonempty_string("retry_provenance", field, getattr(self, field))
        _require_digest(
            "retry_provenance",
            "retry_intent_digest",
            self.retry_intent_digest,
        )
        _require_positive_integer(
            "retry_provenance",
            "source_attempt_no",
            self.source_attempt_no,
        )
        _require_positive_integer("retry_provenance", "source_fence", self.source_fence)
        _require_digest(
            "retry_provenance",
            "adjudication_digest",
            self.adjudication_digest,
        )
        if not isinstance(self.decision, UnknownAdjudicationDecision):
            _invalid("retry_provenance", "decision", "unknown")

    @classmethod
    def from_intent(cls, intent: RetryIntent) -> RetryProvenance:
        """Copy every authority-bearing field from the consumed intent."""
        return cls(
            retry_intent_id=intent.id,
            retry_intent_digest=intent.digest,
            source_attempt_id=intent.source_attempt_id,
            source_attempt_no=intent.source_attempt_no,
            source_fence=intent.source_fence,
            adjudication_id=intent.adjudication_id,
            adjudication_digest=intent.adjudication_digest,
            decision=intent.decision,
        )


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


def _require_positive_integer(entity_type: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(entity_type, field, "not_integer")
    if value < 1:
        _invalid(entity_type, field, "not_positive")


def _require_digest(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity_type, field, "not_digest")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity_type, field, "not_utc")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(
        entity_type=entity_type,
        field=field,
        reason=reason,
    )
