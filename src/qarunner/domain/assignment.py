"""Assignment reservation bound to one Worker generation and execution spec."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.worker import WorkerRef


class AssignmentState(enum.StrEnum):
    """Pre-execution states for one short-lived Assignment."""

    OFFERED = "offered"
    CLAIMED = "claimed"
    COMMITTED = "committed"


@dataclass(frozen=True, slots=True)
class Assignment:
    """Immutable reservation for a Run on a specific Worker generation."""

    id: str
    worker: WorkerRef
    spec_digest: Digest
    state: AssignmentState
    retry_intent_id: str | None = None

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
        if self.retry_intent_id is not None and (
            not isinstance(self.retry_intent_id, str) or not self.retry_intent_id.strip()
        ):
            _invalid("retry_intent_id", "invalid")

    @classmethod
    def offer(
        cls,
        *,
        assignment_id: str,
        worker: WorkerRef,
        spec_digest: Digest,
        retry_intent_id: str | None = None,
    ) -> Assignment:
        """Create an offered Assignment."""
        return cls(
            id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
            state=AssignmentState.OFFERED,
            retry_intent_id=retry_intent_id,
        )

    def claim(self) -> Assignment:
        """Accept the offer for its bound Worker generation."""
        return replace(self, state=AssignmentState.CLAIMED)

    def commit(self) -> Assignment:
        """Bind the claimed Assignment to a durable Attempt."""
        return replace(self, state=AssignmentState.COMMITTED)


def _invalid(field: str, reason: str) -> None:
    raise DomainValidationError(
        entity_type="assignment",
        field=field,
        reason=reason,
    )
