"""Worker identity value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkerRef:
    """Identity of one immutable Worker generation."""

    worker_id: str
    generation: int
