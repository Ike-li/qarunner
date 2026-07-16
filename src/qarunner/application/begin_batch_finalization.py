"""Fact-aware application handler for RUNNING -> FINALIZING."""

from dataclasses import dataclass

from qarunner.application.ports.batch_finalization_readiness import (
    BatchFinalizationReadinessGateway,
    BatchFinalizationReadinessProjection,
    BatchFinalizationReadinessPublication,
    BatchFinalizationReadinessReason,
    BatchFinalizationReadinessSideEffect,
)
from qarunner.domain.batch import BatchState
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict, VersionConflict


@dataclass(frozen=True, slots=True)
class BeginBatchFinalizationCommand:
    batch_id: str
    expected_batch_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, str) or not self.batch_id.strip():
            raise DomainValidationError(
                entity_type="begin_batch_finalization_command", field="batch_id", reason="invalid"
            )
        if (
            isinstance(self.expected_batch_version, bool)
            or not isinstance(self.expected_batch_version, int)
            or self.expected_batch_version < 0
        ):
            raise DomainValidationError(
                entity_type="begin_batch_finalization_command",
                field="expected_batch_version",
                reason="invalid",
            )


@dataclass(frozen=True, slots=True)
class BeginBatchFinalizationResult:
    projection: BatchFinalizationReadinessProjection | None
    replayed: bool
    reason: BatchFinalizationReadinessReason


class BeginBatchFinalization:
    def __init__(self, *, gateway: BatchFinalizationReadinessGateway) -> None:
        self._gateway = gateway

    async def execute(
        self, command: BeginBatchFinalizationCommand
    ) -> BeginBatchFinalizationResult:
        authority = await self._gateway.require_readiness_authority(batch_id=command.batch_id)
        snapshot = authority.snapshot
        if snapshot.reason is not BatchFinalizationReadinessReason.READY:
            return BeginBatchFinalizationResult(None, False, snapshot.reason)
        stored = await self._gateway.lookup_stored(identity_scope=authority.identity_scope)
        if stored is not None:
            if stored.readiness_digest != snapshot.readiness_digest:
                raise IdempotencyConflict(
                    scope=f"batch:{snapshot.batch_id}:begin-finalization",
                    key=str(snapshot.source_batch_version),
                    stored_digest=stored.readiness_digest,
                    received_digest=snapshot.readiness_digest,
                )
            return BeginBatchFinalizationResult(
                stored, True, BatchFinalizationReadinessReason.READY
            )
        locked = await self._gateway.get_mutation_snapshot_for_update(batch_id=snapshot.batch_id)
        if locked.batch_version != command.expected_batch_version:
            raise VersionConflict(
                entity_type="batch",
                entity_id=snapshot.batch_id,
                current_version=locked.batch_version,
                expected_version=command.expected_batch_version,
            )
        if locked.readiness_snapshot != snapshot:
            raise DomainValidationError(
                entity_type="begin_batch_finalization",
                field="readiness_snapshot",
                reason="locked_snapshot_drift",
            )
        projection = BatchFinalizationReadinessProjection(
            snapshot.batch_id,
            snapshot.source_batch_version,
            locked.batch_version + 1,
            BatchState.FINALIZING,
            snapshot.readiness_digest,
        )
        publication = BatchFinalizationReadinessPublication(
            authority,
            locked,
            projection,
            BatchFinalizationReadinessSideEffect(snapshot.batch_id, snapshot.readiness_digest),
        )
        published = await self._gateway.publish_readiness(publication=publication)
        return BeginBatchFinalizationResult(
            published.value, published.replayed, BatchFinalizationReadinessReason.READY
        )
