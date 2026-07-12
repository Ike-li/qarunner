"""Assignment reservation bound to one Worker generation and execution spec."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.digest import Digest
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

    @classmethod
    def offer(cls, *, assignment_id: str, worker: WorkerRef, spec_digest: Digest) -> Assignment:
        """Create an offered Assignment."""
        return cls(
            id=assignment_id,
            worker=worker,
            spec_digest=spec_digest,
            state=AssignmentState.OFFERED,
        )

    def claim(self) -> Assignment:
        """Accept the offer for its bound Worker generation."""
        return replace(self, state=AssignmentState.CLAIMED)

    def commit(self) -> Assignment:
        """Bind the claimed Assignment to a durable Attempt."""
        return replace(self, state=AssignmentState.COMMITTED)
