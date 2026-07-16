"""Immutable facts for control-plane unknown classifications."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError


class UnknownReason(enum.StrEnum):
    """Why the control plane cannot prove a committed Attempt stopped."""

    WORKER_LOST_AFTER_COMMIT = "worker_lost_after_commit"
    CANCEL_STOP_UNPROVEN = "cancel_stop_unproven"
    LEASE_STOP_UNPROVEN = "lease_stop_unproven"
    RECONCILE_AMBIGUOUS = "reconcile_ambiguous"
    EXECUTION_STOP_UNPROVEN = "execution_stop_unproven"


class UnknownSource(enum.StrEnum):
    """Trusted control-plane component that recorded the observation."""

    COORDINATOR = "coordinator"
    CANCEL_CONVERGENCE = "cancel_convergence"
    LEASE_WATCHDOG = "lease_watchdog"
    RECONCILER = "reconciler"


class UnknownAdjudicationDecision(enum.StrEnum):
    """Detailed-design decisions that derive handling without rewriting unknown."""

    CONFIRM_STOPPED_THEN_RETRY = "confirm_stopped_then_retry"
    ACCEPT_DUPLICATE_RISK_THEN_RETRY = "accept_duplicate_risk_then_retry"
    MARK_INFRA_FAILED_NO_RETRY = "mark_infra_failed_no_retry"
    MARK_COMPLETED_FROM_VERIFIED_EVIDENCE = "mark_completed_from_verified_evidence"

    @property
    def permits_retry(self) -> bool:
        return self in {
            self.CONFIRM_STOPPED_THEN_RETRY,
            self.ACCEPT_DUPLICATE_RISK_THEN_RETRY,
        }


@dataclass(frozen=True, slots=True)
class UnknownObservation:
    """Frozen review input attached when an Attempt becomes unknown."""

    id: str
    reason: UnknownReason
    source: UnknownSource
    review_basis_digest: Digest
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.id, str):
            _invalid("unknown_observation", "id", "not_string")
        if not self.id.strip():
            _invalid("unknown_observation", "id", "empty")
        if not isinstance(self.reason, UnknownReason):
            _invalid("unknown_observation", "reason", "unknown")
        if not isinstance(self.source, UnknownSource):
            _invalid("unknown_observation", "source", "unknown")
        if not isinstance(self.review_basis_digest, Digest):
            _invalid("unknown_observation", "review_basis_digest", "not_digest")
        _require_utc("unknown_observation", "recorded_at", self.recorded_at)

    @property
    def digest(self) -> Digest:
        """Rebuild the immutable observation digest referenced by adjudication."""
        return canonical_digest(
            schema_version="qep.unknown-observation.v1",
            payload={
                "id": self.id,
                "reason": self.reason.value,
                "source": self.source.value,
                "review_basis_digest": self.review_basis_digest.value,
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class UnknownAdjudication:
    """Append-only decision; risk_acceptance_digest binds the pre-intent basis."""

    id: str
    attempt_id: str
    unknown_observation_digest: Digest
    decision: UnknownAdjudicationDecision
    actor_id: str
    reason: str
    occurred_at: datetime
    proof_digest: Digest | None
    risk_approver_id: str | None
    risk_acceptance_digest: Digest | None
    evidence_root_digest: Digest | None
    supersedes_adjudication_id: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string("unknown_adjudication", "id", self.id)
        _require_nonempty_string("unknown_adjudication", "attempt_id", self.attempt_id)
        _require_digest(
            "unknown_adjudication",
            "unknown_observation_digest",
            self.unknown_observation_digest,
        )
        if not isinstance(self.decision, UnknownAdjudicationDecision):
            _invalid("unknown_adjudication", "decision", "unknown")
        _require_nonempty_string("unknown_adjudication", "actor_id", self.actor_id)
        _require_nonempty_string("unknown_adjudication", "reason", self.reason)
        _require_utc("unknown_adjudication", "occurred_at", self.occurred_at)
        for field, value in (
            ("proof_digest", self.proof_digest),
            ("risk_acceptance_digest", self.risk_acceptance_digest),
            ("evidence_root_digest", self.evidence_root_digest),
        ):
            if value is not None:
                _require_digest("unknown_adjudication", field, value)
        if self.risk_approver_id is not None:
            _require_nonempty_string(
                "unknown_adjudication",
                "risk_approver_id",
                self.risk_approver_id,
            )
        if self.supersedes_adjudication_id is not None:
            _require_nonempty_string(
                "unknown_adjudication",
                "supersedes_adjudication_id",
                self.supersedes_adjudication_id,
            )
        if self.decision is UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY:
            if self.proof_digest is None:
                _invalid(
                    "unknown_adjudication",
                    "proof_digest",
                    "required_for_decision",
                )
            _forbid_authority_basis(
                self,
                "risk_approver_id",
                "risk_acceptance_digest",
                "evidence_root_digest",
            )
        elif self.decision is UnknownAdjudicationDecision.ACCEPT_DUPLICATE_RISK_THEN_RETRY:
            if self.risk_approver_id is None:
                _invalid(
                    "unknown_adjudication",
                    "risk_approver_id",
                    "required_for_decision",
                )
            if self.risk_acceptance_digest is None:
                _invalid(
                    "unknown_adjudication",
                    "risk_acceptance_digest",
                    "required_for_decision",
                )
            _forbid_authority_basis(self, "proof_digest", "evidence_root_digest")
        elif self.decision is UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY:
            _forbid_authority_basis(
                self,
                "proof_digest",
                "risk_approver_id",
                "risk_acceptance_digest",
                "evidence_root_digest",
            )
        else:
            if self.evidence_root_digest is None:
                _invalid(
                    "unknown_adjudication",
                    "evidence_root_digest",
                    "required_for_decision",
                )
            _forbid_authority_basis(
                self,
                "proof_digest",
                "risk_approver_id",
                "risk_acceptance_digest",
            )

    @property
    def digest(self) -> Digest:
        """Bind retry authorization to the complete append-only decision fact."""
        return canonical_digest(
            schema_version="qep.unknown-adjudication.v1",
            payload={
                "id": self.id,
                "attempt_id": self.attempt_id,
                "unknown_observation_digest": self.unknown_observation_digest.value,
                "decision": self.decision.value,
                "actor_id": self.actor_id,
                "reason": self.reason,
                "occurred_at": self.occurred_at.isoformat().replace("+00:00", "Z"),
                "proof_digest": (
                    self.proof_digest.value if self.proof_digest is not None else None
                ),
                "risk_approver_id": self.risk_approver_id,
                "risk_acceptance_digest": (
                    self.risk_acceptance_digest.value
                    if self.risk_acceptance_digest is not None
                    else None
                ),
                "evidence_root_digest": (
                    self.evidence_root_digest.value
                    if self.evidence_root_digest is not None
                    else None
                ),
                "supersedes_adjudication_id": self.supersedes_adjudication_id,
            },
        )


def _forbid_authority_basis(record: UnknownAdjudication, *fields: str) -> None:
    for field in fields:
        if getattr(record, field) is not None:
            _invalid(
                "unknown_adjudication",
                field,
                "not_allowed_for_decision",
            )


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


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
