"""Canonical events reported for one Attempt."""

from dataclasses import dataclass

from qarunner.domain.digest import Digest


@dataclass(frozen=True, slots=True)
class AttemptEvent:
    """Immutable event identity and payload digest from a Worker."""

    event_id: str
    event_seq: int
    event_type: str
    payload_digest: Digest
