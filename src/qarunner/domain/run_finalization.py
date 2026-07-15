"""V1 Run phase, disposition, and outcome cross-field values."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from qarunner.domain.attempt import AttemptState
from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError


class RunPhase(enum.StrEnum):
    """Orchestration lifecycle, independent from execution outcome."""

    PLANNED = "planned"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    RETRY_QUEUED = "retry_queued"
    CLOSED = "closed"


class RunDisposition(enum.StrEnum):
    """Policy handling state for the latest authoritative execution fact."""

    REVIEW_REQUIRED = "review_required"
    RETRY_QUEUED = "retry_queued"
    CLOSED_NO_RETRY = "closed_no_retry"


class RunOutcome(enum.StrEnum):
    """Persisted policy outcome available only for a closed Run."""

    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RunFinalizationState:
    """Pure v1 cross-field projection without changing the legacy Run writer."""

    phase: RunPhase
    disposition: RunDisposition | None
    outcome: RunOutcome | None
    finalization_basis_digest: Digest | None
    latest_attempt_state: AttemptState | None
    current_assignment_id: str | None
    pending_retry_intent_id: str | None

    def __post_init__(self) -> None:
        entity = "run_finalization_state"
        _require_optional_enum(entity, "phase", self.phase, RunPhase, required=True)
        _require_optional_enum(entity, "disposition", self.disposition, RunDisposition)
        _require_optional_enum(entity, "outcome", self.outcome, RunOutcome)
        _require_optional_enum(
            entity,
            "latest_attempt_state",
            self.latest_attempt_state,
            AttemptState,
        )
        if self.finalization_basis_digest is not None and not isinstance(
            self.finalization_basis_digest, Digest
        ):
            _invalid(entity, "finalization_basis_digest", "not_digest")
        for field in ("current_assignment_id", "pending_retry_intent_id"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                _invalid(entity, field, "invalid")
        self._validate_closed(entity)
        self._validate_review(entity)
        self._validate_retry(entity)

    def _validate_closed(self, entity: str) -> None:
        if self.phase is RunPhase.CLOSED:
            if self.disposition is not RunDisposition.CLOSED_NO_RETRY:
                _invalid(entity, "disposition", "closed_requires_disposition")
            if self.outcome is None:
                _invalid(entity, "outcome", "closed_requires_outcome")
            if self.finalization_basis_digest is None:
                _invalid(entity, "finalization_basis_digest", "closed_requires_basis")
            if self.current_assignment_id is not None or self.pending_retry_intent_id is not None:
                _invalid(entity, "phase", "closed_forbids_active_pointer")
            return
        if self.disposition is RunDisposition.CLOSED_NO_RETRY:
            _invalid(entity, "disposition", "closed_disposition_requires_closed_phase")
        if self.outcome is not None:
            _invalid(entity, "outcome", "outcome_requires_closed")
        if self.finalization_basis_digest is not None:
            _invalid(entity, "finalization_basis_digest", "basis_requires_closed")

    def _validate_review(self, entity: str) -> None:
        if self.disposition is not RunDisposition.REVIEW_REQUIRED:
            return
        if self.phase is not RunPhase.RUNNING:
            _invalid(entity, "disposition", "review_requires_running")
        if self.latest_attempt_state is not AttemptState.ATTEMPT_UNKNOWN:
            _invalid(entity, "latest_attempt_state", "review_requires_unknown")

    def _validate_retry(self, entity: str) -> None:
        if self.phase is RunPhase.RETRY_QUEUED:
            if self.disposition is not RunDisposition.RETRY_QUEUED:
                _invalid(entity, "disposition", "retry_requires_disposition")
            if self.pending_retry_intent_id is None:
                _invalid(
                    entity,
                    "pending_retry_intent_id",
                    "retry_requires_pending_intent",
                )
            return
        if self.disposition is RunDisposition.RETRY_QUEUED:
            _invalid(entity, "disposition", "retry_disposition_requires_retry_phase")


def _require_optional_enum(
    entity: str,
    field: str,
    value: object,
    expected_type: type[enum.StrEnum],
    *,
    required: bool = False,
) -> None:
    if value is None and not required:
        return
    if not isinstance(value, expected_type):
        _invalid(entity, field, "unknown")


def _invalid(entity: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity, field=field, reason=reason)
