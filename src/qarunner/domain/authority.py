"""Authoritative execution identity snapshots supplied by the control plane."""

from dataclasses import dataclass

from qarunner.domain.errors import DomainValidationError
from qarunner.domain.worker import WorkerRef


@dataclass(frozen=True, slots=True)
class AttemptAuthority:
    """Current Run fence and Worker generation from authoritative aggregates."""

    current_fence: int
    current_worker: WorkerRef

    def __post_init__(self) -> None:
        if isinstance(self.current_fence, bool) or not isinstance(self.current_fence, int):
            raise DomainValidationError(
                entity_type="attempt_authority",
                field="current_fence",
                reason="not_integer",
            )
        if self.current_fence < 1:
            raise DomainValidationError(
                entity_type="attempt_authority",
                field="current_fence",
                reason="not_positive",
            )
        if not isinstance(self.current_worker, WorkerRef):
            raise DomainValidationError(
                entity_type="attempt_authority",
                field="current_worker",
                reason="invalid_type",
            )


@dataclass(frozen=True, slots=True)
class WorkerAuthority:
    """Registry-owned current generation for one logical Worker."""

    current_ref: WorkerRef
