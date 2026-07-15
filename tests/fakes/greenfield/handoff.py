"""Deterministic delivery Fake for materialized-scope handoffs."""

from qarunner.application.handoff import BatchMaterializedScopeHandoff
from qarunner.application.ports.common import ReplayResult
from qarunner.application.ports.handoff import (
    HandoffDeliveryMetadata,
    HandoffEventBlocked,
    HandoffEventConflict,
    HandoffPoisonAlert,
)


class InMemoryMaterializedScopeHandoffConsumer:
    """Model at-least-once delivery identity without downstream convergence."""

    def __init__(self) -> None:
        self._accepted_by_event_id: dict[str, BatchMaterializedScopeHandoff] = {}
        self._blocked_event_ids: set[str] = set()
        self.quarantined_event_ids: tuple[str, ...] = ()
        self.alerts: tuple[HandoffPoisonAlert, ...] = ()
        self.run_resolutions: tuple[object, ...] = ()
        self.fanout_results: tuple[object, ...] = ()
        self.not_executed_facts: tuple[object, ...] = ()
        self.run_outcomes: tuple[object, ...] = ()
        self.execution_bases: tuple[object, ...] = ()

    @property
    def accepted(self) -> tuple[BatchMaterializedScopeHandoff, ...]:
        return tuple(self._accepted_by_event_id.values())

    async def consume(
        self,
        *,
        handoff: BatchMaterializedScopeHandoff,
        publisher_metadata: HandoffDeliveryMetadata | None = None,
    ) -> ReplayResult[BatchMaterializedScopeHandoff]:
        del publisher_metadata
        if handoff.event_id in self._blocked_event_ids:
            raise HandoffEventBlocked(event_id=handoff.event_id)
        stored = self._accepted_by_event_id.get(handoff.event_id)
        if stored is None:
            self._accepted_by_event_id[handoff.event_id] = handoff
            return ReplayResult(value=handoff, replayed=False)
        if stored.payload_digest == handoff.payload_digest:
            return ReplayResult(value=stored, replayed=True)
        self._blocked_event_ids.add(handoff.event_id)
        self.quarantined_event_ids += (handoff.event_id,)
        self.alerts += (
            HandoffPoisonAlert(
                event_id=handoff.event_id,
                stored_payload_digest=stored.payload_digest,
                received_payload_digest=handoff.payload_digest,
            ),
        )
        raise HandoffEventConflict(
            event_id=handoff.event_id,
            stored_payload_digest=stored.payload_digest,
            received_payload_digest=handoff.payload_digest,
        )
