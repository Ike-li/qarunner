"""Application handler for replaying or publishing a terminal Batch basis."""

from dataclasses import dataclass

from qarunner.application.ports.batch_finalization import (
    BatchFinalizationGateway,
    BatchFinalizationProjection,
    BatchFinalizationPublication,
    BatchFinalizationSideEffect,
    BatchFinalizationSourceSnapshot,
)
from qarunner.domain.batch_finalization import BatchFinalizationBasis
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict, VersionConflict


@dataclass(frozen=True, slots=True)
class FinalizeBatchCommand:
    candidate_basis: BatchFinalizationBasis
    expected_batch_version: int

    def __post_init__(self) -> None:
        entity = "finalize_batch_command"
        if not isinstance(self.candidate_basis, BatchFinalizationBasis):
            raise DomainValidationError(
                entity_type=entity, field="candidate_basis", reason="invalid"
            )
        if (
            isinstance(self.expected_batch_version, bool)
            or not isinstance(self.expected_batch_version, int)
            or self.expected_batch_version < 0
        ):
            raise DomainValidationError(
                entity_type=entity, field="expected_batch_version", reason="invalid"
            )


@dataclass(frozen=True, slots=True)
class FinalizeBatchResult:
    projection: BatchFinalizationProjection
    replayed: bool


class FinalizeBatch:
    def __init__(self, *, gateway: BatchFinalizationGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: FinalizeBatchCommand) -> FinalizeBatchResult:
        basis = command.candidate_basis
        authority = await self._gateway.require_finalization_authority(batch_id=basis.batch_id)
        if authority.source_snapshot != BatchFinalizationSourceSnapshot.from_basis(basis):
            raise DomainValidationError(
                entity_type="finalize_batch",
                field="source_snapshot",
                reason="authority_mismatch",
            )
        stored = await self._gateway.lookup_stored(identity_scope=authority.identity_scope)
        if stored is not None:
            if stored.basis_digest != basis.basis_digest:
                raise IdempotencyConflict(
                    scope=f"batch:{basis.batch_id}:finalization",
                    key=str(basis.source_batch_version),
                    stored_digest=stored.basis_digest,
                    received_digest=basis.basis_digest,
                )
            return FinalizeBatchResult(stored, True)
        snapshot = await self._gateway.get_mutation_snapshot_for_update(batch_id=basis.batch_id)
        if snapshot.batch_version != command.expected_batch_version:
            raise VersionConflict(
                entity_type="batch",
                entity_id=basis.batch_id,
                current_version=snapshot.batch_version,
                expected_version=command.expected_batch_version,
            )
        if snapshot.source_snapshot != authority.source_snapshot:
            raise DomainValidationError(
                entity_type="finalize_batch",
                field="source_snapshot",
                reason="locked_snapshot_drift",
            )
        projection = BatchFinalizationProjection(
            batch_id=basis.batch_id,
            source_batch_version=basis.source_batch_version,
            batch_version=snapshot.batch_version + 1,
            outcome=basis.batch_outcome,
            basis_digest=basis.basis_digest,
        )
        publication = BatchFinalizationPublication(
            authority=authority,
            expected_snapshot=snapshot,
            basis=basis,
            projection=projection,
            side_effect=BatchFinalizationSideEffect(
                batch_id=basis.batch_id,
                basis_digest=basis.basis_digest,
                outcome=basis.batch_outcome,
            ),
        )
        result = await self._gateway.publish_finalization(publication=publication)
        return FinalizeBatchResult(result.value, result.replayed)
