"""Pluggable outbox event delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError
from qarunner.domain.digest import Digest


@dataclass(frozen=True, slots=True)
class OutboxDeliveryEvent:
    """One committed outbox row offered to a sink for at-least-once delivery."""

    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload_digest: Digest
    payload: dict[str, object]
    delivery_attempt: int

    def __post_init__(self) -> None:
        entity = "outbox_delivery_event"
        for field in ("event_id", "aggregate_type", "aggregate_id", "event_type"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise PortContractError(resource=entity, field=field, reason="invalid")
        if not isinstance(self.payload_digest, Digest):
            raise PortContractError(resource=entity, field="payload_digest", reason="not_digest")
        if not isinstance(self.payload, dict):
            raise PortContractError(resource=entity, field="payload", reason="not_dict")
        if (
            isinstance(self.delivery_attempt, bool)
            or not isinstance(self.delivery_attempt, int)
            or self.delivery_attempt < 1
        ):
            raise PortContractError(resource=entity, field="delivery_attempt", reason="invalid")


@runtime_checkable
class OutboxSink(Protocol):
    """External at-least-once delivery boundary.

    Publisher lease/attempt/status metadata never crosses into this boundary or into any
    domain basis; the sink only ever sees the committed event identity, type, and payload.
    """

    async def deliver(self, event: OutboxDeliveryEvent) -> None: ...
