"""Deterministic provider and delivery consumer for Run-closed handoffs."""

from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.run_closed_handoff import (
    RunClosedDeliveryMetadata,
    RunClosedHandoffBlocked,
    RunClosedHandoffConflict,
    RunClosedPoisonAlert,
)
from qarunner.application.run_closed_handoff import RunClosedHandoff
from qarunner.domain import Digest, IdempotencyConflict


class InMemoryRunClosedHandoffConsumer:
    def __init__(self) -> None:
        self.accepted: dict[str, RunClosedHandoff] = {}
        self.blocked: set[str] = set()
        self.batch_finalizations: tuple[object, ...] = ()
        self.quarantined_event_ids: tuple[str, ...] = ()
        self.alerts: tuple[RunClosedPoisonAlert, ...] = ()

    async def consume(
        self,
        *,
        handoff: RunClosedHandoff,
        publisher_metadata: RunClosedDeliveryMetadata | None = None,
    ) -> ReplayResult[RunClosedHandoff]:
        del publisher_metadata
        handoff.validate_self_consistency()
        if handoff.event_id in self.blocked:
            raise RunClosedHandoffBlocked(event_id=handoff.event_id)
        stored = self.accepted.get(handoff.event_id)
        if stored is None:
            self.accepted[handoff.event_id] = handoff
            return ReplayResult(handoff, False)
        if stored.payload_digest == handoff.payload_digest:
            return ReplayResult(stored, True)
        self.blocked.add(handoff.event_id)
        self.quarantined_event_ids += (handoff.event_id,)
        self.alerts += (
            RunClosedPoisonAlert(handoff.event_id, stored.payload_digest, handoff.payload_digest),
        )
        raise RunClosedHandoffConflict(
            event_id=handoff.event_id,
            stored_payload_digest=stored.payload_digest,
            received_payload_digest=handoff.payload_digest,
        )


class InMemoryRunClosedFactProvider:
    def __init__(self, gateway) -> None:
        self.gateway = gateway

    async def get_basis(self, *, run_id: str, source_run_version: int, basis_digest: Digest):
        scope = ("qep.run-finalization-basis.v1", run_id, source_run_version)
        basis = self.gateway.bases.get(scope)
        if basis is None:
            raise KeyError(scope)
        if basis.basis_digest != basis_digest:
            raise IdempotencyConflict(
                scope=f"run:{run_id}:finalization",
                key=str(source_run_version),
                stored_digest=basis.basis_digest,
                received_digest=basis_digest,
            )
        return basis

    async def get_resolution_set(self, *, digest: Digest):
        found = [
            value
            for value in self.gateway.resolution_sets.values()
            if value.resolution_set_digest == digest
        ]
        if not found:
            raise KeyError(digest.value)
        if len(found) != 1:
            raise RuntimeError("run_closed_fact/duplicate_resolution_digest")
        return found[0]
