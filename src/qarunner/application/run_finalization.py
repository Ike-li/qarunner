"""Application handler for atomically publishing a terminal Run basis."""

from dataclasses import dataclass

from qarunner.application.ports.run_finalization import (
    RunFinalizationGateway,
    RunFinalizationProjection,
    RunFinalizationPublication,
    RunFinalizationSideEffect,
)
from qarunner.application.run_closed_handoff import build_run_closed_handoff
from qarunner.domain.errors import DomainValidationError, IdempotencyConflict, VersionConflict
from qarunner.domain.run_finalization import (
    RunDisposition,
    RunFinalizationBasis,
    RunFinalizationState,
    RunItemResolutionSet,
    RunPhase,
)


@dataclass(frozen=True, slots=True)
class FinalizeRunCommand:
    candidate_basis: RunFinalizationBasis
    resolution_set: RunItemResolutionSet
    expected_run_version: int
    expected_attempt_version: int | None

    def __post_init__(self) -> None:
        entity = "finalize_run_command"
        if not isinstance(self.candidate_basis, RunFinalizationBasis):
            raise DomainValidationError(
                entity_type=entity, field="candidate_basis", reason="invalid"
            )
        if not isinstance(self.resolution_set, RunItemResolutionSet):
            raise DomainValidationError(
                entity_type=entity, field="resolution_set", reason="invalid"
            )
        _require_nonnegative_version(entity, "expected_run_version", self.expected_run_version)
        if self.expected_attempt_version is not None:
            _require_nonnegative_version(
                entity, "expected_attempt_version", self.expected_attempt_version
            )
        basis = self.candidate_basis
        resolutions = self.resolution_set
        if (
            basis.run_id != resolutions.run_id
            or basis.batch_id != resolutions.batch_id
            or basis.source_run_version != resolutions.source_run_version
            or basis.item_resolution_set_digest != resolutions.resolution_set_digest
            or basis.original_resolution_set_digest != resolutions.original_resolution_set_digest
            or basis.effective_resolution_set_digest != resolutions.effective_resolution_set_digest
            or basis.outcome is not resolutions.audit_outcome
        ):
            raise DomainValidationError(
                entity_type=entity, field="resolution_set", reason="basis_mismatch"
            )
        if (basis.final_attempt_id is None) != (self.expected_attempt_version is None):
            raise DomainValidationError(
                entity_type=entity, field="expected_attempt_version", reason="attempt_mismatch"
            )


@dataclass(frozen=True, slots=True)
class FinalizeRunResult:
    projection: RunFinalizationProjection
    replayed: bool


class FinalizeRun:
    def __init__(self, *, gateway: RunFinalizationGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: FinalizeRunCommand) -> FinalizeRunResult:
        basis = command.candidate_basis
        authority = await self._gateway.require_finalization_authority(run_id=basis.run_id)
        if (authority.batch_id, authority.source_run_version) != (
            basis.batch_id,
            basis.source_run_version,
        ):
            raise VersionConflict(
                entity_type="run",
                entity_id=basis.run_id,
                current_version=authority.source_run_version,
                expected_version=basis.source_run_version,
            )
        stored = await self._gateway.lookup_stored(identity_scope=authority.identity_scope)
        if stored is not None:
            if stored.basis_digest != basis.basis_digest:
                raise IdempotencyConflict(
                    scope=f"run:{basis.run_id}:finalization",
                    key=str(basis.source_run_version),
                    stored_digest=stored.basis_digest,
                    received_digest=basis.basis_digest,
                )
            return FinalizeRunResult(stored, True)
        snapshot = await self._gateway.get_mutation_snapshot_for_update(run_id=basis.run_id)
        if snapshot.attempt_version != command.expected_attempt_version:
            raise VersionConflict(
                entity_type="attempt",
                entity_id=snapshot.attempt_id or "prestart",
                current_version=snapshot.attempt_version or 0,
                expected_version=command.expected_attempt_version or 0,
            )
        if snapshot.run_version != command.expected_run_version:
            raise VersionConflict(
                entity_type="run",
                entity_id=basis.run_id,
                current_version=snapshot.run_version,
                expected_version=command.expected_run_version,
            )
        state = RunFinalizationState(
            phase=RunPhase.CLOSED,
            disposition=RunDisposition.CLOSED_NO_RETRY,
            outcome=basis.outcome,
            finalization_basis_digest=basis.basis_digest,
            latest_attempt_fact=basis.final_attempt_state,
            current_assignment_id=None,
            pending_retry_intent_id=None,
        )
        projection = RunFinalizationProjection(basis.run_id, basis.source_run_version, state)
        publication = RunFinalizationPublication(
            authority,
            snapshot,
            basis,
            command.resolution_set,
            projection,
            RunFinalizationSideEffect(basis.run_id, basis.basis_digest, basis.outcome),
            build_run_closed_handoff(
                basis=basis,
                resolution_set=command.resolution_set,
                authority_digest=authority.authority_digest,
                write_epoch=authority.write_epoch,
            ),
        )
        result = await self._gateway.publish_finalization(publication=publication)
        return FinalizeRunResult(result.value, result.replayed)


def _require_nonnegative_version(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(entity_type=entity, field=field, reason="invalid")
