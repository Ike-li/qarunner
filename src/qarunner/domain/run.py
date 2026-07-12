"""Run aggregate for the greenfield scheduling lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from qarunner.domain.errors import InvalidTransition, ensure_expected_version


class RunState(enum.StrEnum):
    """States required before a Run may own an Assignment."""

    PLANNED = "planned"
    QUEUED = "queued"
    ASSIGNED = "assigned"


_ALLOWED_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PLANNED: frozenset({RunState.QUEUED}),
    RunState.QUEUED: frozenset({RunState.ASSIGNED}),
    RunState.ASSIGNED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Run:
    """Immutable Run state; Assignment/Attempt semantics are added incrementally."""

    id: str
    state: RunState
    version: int

    @classmethod
    def create(cls, *, run_id: str) -> Run:
        """Create a planned Run."""
        return cls(id=run_id, state=RunState.PLANNED, version=0)

    def transition(self, target: RunState, *, expected_version: int) -> Run:
        """Reject stale commands or state edges that skip scheduling phases."""
        ensure_expected_version(
            entity_type="run",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="run",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(self, state=target, version=self.version + 1)
