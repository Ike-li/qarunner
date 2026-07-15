"""Delivery boundary for immutable materialized-scope handoff events."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.handoff import BatchMaterializedScopeHandoff
from qarunner.application.ports.common import ReplayResult
from qarunner.domain.digest import Digest


@dataclass(frozen=True, slots=True)
class HandoffDeliveryMetadata:
    """Mutable delivery-plane claims excluded from immutable handoff identity."""

    claim_id: str
    attempt: int
    status: str


class HandoffEventConflict(RuntimeError):
    """One event identity was delivered with a different immutable payload."""

    code = "handoff_event/poison_payload"

    def __init__(
        self,
        *,
        event_id: str,
        stored_payload_digest: Digest,
        received_payload_digest: Digest,
    ) -> None:
        self.event_id = event_id
        self.stored_payload_digest = stored_payload_digest
        self.received_payload_digest = received_payload_digest
        super().__init__(f"handoff event {event_id} has conflicting payload digests")


class HandoffEventBlocked(RuntimeError):
    """A poisoned event remains stopped until an explicit repair decision."""

    code = "handoff_event/blocked"

    def __init__(self, *, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(f"handoff event {event_id} is blocked")


@dataclass(frozen=True, slots=True)
class HandoffPoisonAlert:
    event_id: str
    stored_payload_digest: Digest
    received_payload_digest: Digest
    severity: str = "high"


@runtime_checkable
class MaterializedScopeHandoffConsumer(Protocol):
    """Idempotently accept delivery without performing 001G/001H domain work."""

    async def consume(
        self,
        *,
        handoff: BatchMaterializedScopeHandoff,
        publisher_metadata: HandoffDeliveryMetadata | None = None,
    ) -> ReplayResult[BatchMaterializedScopeHandoff]: ...
