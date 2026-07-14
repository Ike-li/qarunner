"""Batch aggregate for the greenfield execution lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.errors import (
    DomainValidationError,
    InvalidTransition,
    ensure_expected_version,
)


class BatchState(enum.StrEnum):
    """States of one immutable Batch aggregate."""

    DRAFT = "draft"
    VALIDATING = "validating"
    COLLECTING = "collecting"
    PLANNING = "planning"
    AWAITING_ADMISSION = "awaiting_admission"
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


_ALLOWED_TRANSITIONS: dict[BatchState, frozenset[BatchState]] = {
    BatchState.DRAFT: frozenset({BatchState.VALIDATING}),
    BatchState.VALIDATING: frozenset({BatchState.COLLECTING, BatchState.REJECTED}),
    BatchState.COLLECTING: frozenset({BatchState.PLANNING}),
    BatchState.PLANNING: frozenset({BatchState.AWAITING_ADMISSION}),
    BatchState.AWAITING_ADMISSION: frozenset({BatchState.QUEUED, BatchState.REJECTED}),
    BatchState.QUEUED: frozenset({BatchState.RUNNING, BatchState.CANCELLED}),
    BatchState.RUNNING: frozenset({BatchState.FINALIZING}),
    # Terminal classification is deliberately not exposed through transition().
    # A later M0 slice adds a finalize command that enforces Run/Evidence facts.
    BatchState.FINALIZING: frozenset(),
    BatchState.SUCCEEDED: frozenset(),
    BatchState.FAILED: frozenset(),
    BatchState.PARTIAL: frozenset(),
    BatchState.CANCELLED: frozenset(),
    BatchState.REJECTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Batch:
    """Immutable Batch state; successful commands return a new version."""

    id: str
    state: BatchState
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise DomainValidationError(
                entity_type="batch",
                field="id",
                reason="invalid",
            )
        if not isinstance(self.state, BatchState):
            raise DomainValidationError(
                entity_type="batch",
                field="state",
                reason="unknown",
            )
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise DomainValidationError(
                entity_type="batch",
                field="version",
                reason="invalid",
            )

    @classmethod
    def create(cls, *, batch_id: str) -> Batch:
        """Create a Batch at the initial draft state."""
        return cls(id=batch_id, state=BatchState.DRAFT, version=0)

    def transition(self, target: BatchState, *, expected_version: int) -> Batch:
        """Return the next Batch version or reject an illegal state edge."""
        ensure_expected_version(
            entity_type="batch",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if not isinstance(target, BatchState):
            raise DomainValidationError(
                entity_type="batch",
                field="state",
                reason="unknown",
            )
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(self, state=target, version=self.version + 1)
