"""Authoritative execution identity snapshots supplied by the control plane."""

from dataclasses import dataclass

from qarunner.domain.worker import WorkerRef


@dataclass(frozen=True, slots=True)
class AttemptAuthority:
    """Current Run fence and Worker generation from authoritative aggregates."""

    current_fence: int
    current_worker: WorkerRef
