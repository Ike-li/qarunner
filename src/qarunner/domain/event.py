"""Canonical events reported for one Attempt."""

from dataclasses import dataclass

from qarunner.domain.digest import Digest
from qarunner.domain.errors import DomainValidationError


@dataclass(frozen=True, slots=True)
class AttemptEvent:
    """Immutable event identity and payload digest from a Worker."""

    event_id: str
    event_seq: int
    event_type: str
    payload_digest: Digest

    def __post_init__(self) -> None:
        for field, value in (
            ("event_id", self.event_id),
            ("event_type", self.event_type),
        ):
            if not isinstance(value, str):
                raise DomainValidationError(
                    entity_type="attempt_event",
                    field=field,
                    reason="not_string",
                )
            if not value.strip():
                raise DomainValidationError(
                    entity_type="attempt_event",
                    field=field,
                    reason="empty",
                )
        if isinstance(self.event_seq, bool) or not isinstance(self.event_seq, int):
            raise DomainValidationError(
                entity_type="attempt_event",
                field="event_seq",
                reason="not_integer",
            )
        if self.event_seq < 1:
            raise DomainValidationError(
                entity_type="attempt_event",
                field="event_seq",
                reason="not_positive",
            )
        if not isinstance(self.payload_digest, Digest):
            raise DomainValidationError(
                entity_type="attempt_event",
                field="payload_digest",
                reason="not_digest",
            )
