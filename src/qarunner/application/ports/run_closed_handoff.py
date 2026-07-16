"""Provider and at-least-once consumer boundary for immutable Run-closed facts."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import ReplayResult
from qarunner.application.run_closed_handoff import RunClosedHandoff
from qarunner.domain.digest import Digest
from qarunner.domain.run_finalization import RunFinalizationBasis, RunItemResolutionSet


@dataclass(frozen=True, slots=True)
class RunClosedDeliveryMetadata:
    claim_id: str
    attempt: int
    status: str


class RunClosedHandoffConflict(RuntimeError):
    code = "run_closed_handoff/poison_payload"

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
        super().__init__(event_id)


class RunClosedHandoffBlocked(RuntimeError):
    code = "run_closed_handoff/blocked"

    def __init__(self, *, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(event_id)


@dataclass(frozen=True, slots=True)
class RunClosedPoisonAlert:
    event_id: str
    stored_payload_digest: Digest
    received_payload_digest: Digest
    severity: str = "high"


@runtime_checkable
class RunClosedFactProvider(Protocol):
    async def get_basis(
        self, *, run_id: str, source_run_version: int, basis_digest: Digest
    ) -> RunFinalizationBasis: ...
    async def get_resolution_set(self, *, digest: Digest) -> RunItemResolutionSet: ...


@runtime_checkable
class RunClosedHandoffConsumer(Protocol):
    async def consume(
        self,
        *,
        handoff: RunClosedHandoff,
        publisher_metadata: RunClosedDeliveryMetadata | None = None,
    ) -> ReplayResult[RunClosedHandoff]: ...
