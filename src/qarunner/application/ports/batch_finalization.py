"""Narrow authority and replay port for Batch finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.domain.batch import BatchState
from qarunner.domain.batch_finalization import (
    BatchFinalizationBasis,
    BatchNonRunResolutionRef,
    BatchTerminalRunRef,
    BatchUnknownFactRef,
)
from qarunner.domain.digest import Digest

BATCH_FINALIZATION_BASIS_SCHEMA = "qep.batch-finalization-basis.v1"
type BatchFinalizationIdentityScope = tuple[str, str, int]


@dataclass(frozen=True, slots=True)
class BatchFinalizationSourceSnapshot:
    """Frozen authority inputs that a candidate basis must match byte-for-byte."""

    batch_id: str
    source_batch_version: int
    manifest_id: str
    manifest_digest: Digest
    item_count: int
    shard_plan_id: str
    shard_plan_version: int
    shard_plan_digest: Digest
    canonical_run_set_digest: Digest
    success_policy_id: str
    success_policy_version: int
    success_policy_digest: Digest
    batch_item_resolution_set_digest: Digest
    completeness_proof_digest: Digest
    batch_cancellation_intent_digest: Digest | None
    terminal_run_refs: tuple[BatchTerminalRunRef, ...]
    non_run_refs: tuple[BatchNonRunResolutionRef, ...]
    unknown_fact_refs: tuple[BatchUnknownFactRef, ...]

    def __post_init__(self) -> None:
        entity = "batch_finalization_source_snapshot"
        for field in ("batch_id", "manifest_id", "shard_plan_id", "success_policy_id"):
            _require_string(entity, field, getattr(self, field))
        for field in ("source_batch_version", "shard_plan_version"):
            _require_nonnegative_int(entity, field, getattr(self, field))
        _require_positive_int(entity, "item_count", self.item_count)
        _require_positive_int(entity, "success_policy_version", self.success_policy_version)
        for field in (
            "manifest_digest",
            "shard_plan_digest",
            "canonical_run_set_digest",
            "success_policy_digest",
            "batch_item_resolution_set_digest",
            "completeness_proof_digest",
        ):
            _require_digest(entity, field, getattr(self, field))
        if self.batch_cancellation_intent_digest is not None:
            _require_digest(
                entity,
                "batch_cancellation_intent_digest",
                self.batch_cancellation_intent_digest,
            )
        for field, expected_type in (
            ("terminal_run_refs", BatchTerminalRunRef),
            ("non_run_refs", BatchNonRunResolutionRef),
            ("unknown_fact_refs", BatchUnknownFactRef),
        ):
            values = getattr(self, field)
            if not isinstance(values, tuple) or any(
                not isinstance(value, expected_type) for value in values
            ):
                _invalid(entity, field, "invalid")

    @classmethod
    def from_basis(cls, basis: BatchFinalizationBasis) -> BatchFinalizationSourceSnapshot:
        if not isinstance(basis, BatchFinalizationBasis):
            _invalid("batch_finalization_source_snapshot", "basis", "invalid")
        return cls(
            batch_id=basis.batch_id,
            source_batch_version=basis.source_batch_version,
            manifest_id=basis.manifest_id,
            manifest_digest=basis.manifest_digest,
            item_count=basis.item_count,
            shard_plan_id=basis.shard_plan_id,
            shard_plan_version=basis.shard_plan_version,
            shard_plan_digest=basis.shard_plan_digest,
            canonical_run_set_digest=basis.canonical_run_set_digest,
            success_policy_id=basis.success_policy_id,
            success_policy_version=basis.success_policy_version,
            success_policy_digest=basis.success_policy_digest,
            batch_item_resolution_set_digest=basis.batch_item_resolution_set_digest,
            completeness_proof_digest=basis.completeness_proof_digest,
            batch_cancellation_intent_digest=basis.batch_cancellation_intent_digest,
            terminal_run_refs=basis.terminal_run_refs,
            non_run_refs=basis.non_run_refs,
            unknown_fact_refs=basis.unknown_fact_refs,
        )


@dataclass(frozen=True, slots=True)
class FinalizeBatchAuthority:
    """Current single-writer authority for one frozen Batch source snapshot."""

    source_snapshot: BatchFinalizationSourceSnapshot
    authority_digest: Digest
    write_epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_snapshot, BatchFinalizationSourceSnapshot):
            _invalid("finalize_batch_authority", "source_snapshot", "invalid")
        _require_digest("finalize_batch_authority", "authority_digest", self.authority_digest)
        _require_positive_int("finalize_batch_authority", "write_epoch", self.write_epoch)

    @property
    def batch_id(self) -> str:
        return self.source_snapshot.batch_id

    @property
    def source_batch_version(self) -> int:
        return self.source_snapshot.source_batch_version

    @property
    def identity_scope(self) -> BatchFinalizationIdentityScope:
        return (BATCH_FINALIZATION_BASIS_SCHEMA, self.batch_id, self.source_batch_version)


@dataclass(frozen=True, slots=True)
class BatchFinalizationProjection:
    """Persisted Batch terminal projection returned by first write and replay."""

    batch_id: str
    source_batch_version: int
    batch_version: int
    outcome: BatchState
    basis_digest: Digest

    def __post_init__(self) -> None:
        entity = "batch_finalization_projection"
        _require_string(entity, "batch_id", self.batch_id)
        _require_nonnegative_int(entity, "source_batch_version", self.source_batch_version)
        _require_positive_int(entity, "batch_version", self.batch_version)
        if self.outcome not in {
            BatchState.SUCCEEDED,
            BatchState.FAILED,
            BatchState.PARTIAL,
            BatchState.CANCELLED,
        }:
            _invalid(entity, "outcome", "not_execution_terminal")
        _require_digest(entity, "basis_digest", self.basis_digest)


@dataclass(frozen=True, slots=True)
class BatchFinalizationMutationSnapshot:
    """Locked mutable Batch version, read only after stored replay lookup misses."""

    batch_id: str
    batch_version: int
    state: BatchState
    source_snapshot: BatchFinalizationSourceSnapshot

    def __post_init__(self) -> None:
        _require_string("batch_finalization_mutation_snapshot", "batch_id", self.batch_id)
        _require_nonnegative_int(
            "batch_finalization_mutation_snapshot", "batch_version", self.batch_version
        )
        if self.state is not BatchState.FINALIZING:
            _invalid("batch_finalization_mutation_snapshot", "state", "not_finalizing")
        if not isinstance(self.source_snapshot, BatchFinalizationSourceSnapshot):
            _invalid("batch_finalization_mutation_snapshot", "source_snapshot", "invalid")
        if self.batch_id != self.source_snapshot.batch_id:
            _invalid("batch_finalization_mutation_snapshot", "batch_id", "source_mismatch")
        if self.batch_version != self.source_snapshot.source_batch_version:
            _invalid(
                "batch_finalization_mutation_snapshot",
                "batch_version",
                "source_version_mismatch",
            )


@dataclass(frozen=True, slots=True)
class BatchFinalizationSideEffect:
    """Semantic identity shared by terminal audit and outbox publication."""

    batch_id: str
    basis_digest: Digest
    outcome: BatchState

    def __post_init__(self) -> None:
        entity = "batch_finalization_side_effect"
        _require_string(entity, "batch_id", self.batch_id)
        _require_digest(entity, "basis_digest", self.basis_digest)
        if self.outcome not in {
            BatchState.SUCCEEDED,
            BatchState.FAILED,
            BatchState.PARTIAL,
            BatchState.CANCELLED,
        }:
            _invalid(entity, "outcome", "not_execution_terminal")


@dataclass(frozen=True, slots=True)
class BatchFinalizationPublication:
    """One atomic Batch basis, terminal projection, audit, and outbox write."""

    authority: FinalizeBatchAuthority
    expected_snapshot: BatchFinalizationMutationSnapshot
    basis: BatchFinalizationBasis
    projection: BatchFinalizationProjection
    side_effect: BatchFinalizationSideEffect

    def __post_init__(self) -> None:
        entity = "batch_finalization_publication"
        for field, expected_type in (
            ("authority", FinalizeBatchAuthority),
            ("expected_snapshot", BatchFinalizationMutationSnapshot),
            ("basis", BatchFinalizationBasis),
            ("projection", BatchFinalizationProjection),
            ("side_effect", BatchFinalizationSideEffect),
        ):
            if not isinstance(getattr(self, field), expected_type):
                _invalid(entity, field, "invalid")
        if (
            self.authority.batch_id != self.basis.batch_id
            or self.authority.source_batch_version != self.basis.source_batch_version
            or self.authority.source_snapshot
            != BatchFinalizationSourceSnapshot.from_basis(self.basis)
            or self.expected_snapshot.batch_id != self.basis.batch_id
            or self.expected_snapshot.source_snapshot != self.authority.source_snapshot
            or self.projection.batch_id != self.basis.batch_id
            or self.projection.source_batch_version != self.basis.source_batch_version
            or self.projection.batch_version != self.expected_snapshot.batch_version + 1
            or self.projection.outcome is not self.basis.batch_outcome
            or self.projection.basis_digest != self.basis.basis_digest
            or self.side_effect.batch_id != self.basis.batch_id
            or self.side_effect.basis_digest != self.basis.basis_digest
            or self.side_effect.outcome is not self.basis.batch_outcome
        ):
            _invalid(entity, "binding", "mismatch")

    @property
    def identity_scope(self) -> BatchFinalizationIdentityScope:
        return self.authority.identity_scope


@runtime_checkable
class BatchFinalizationGateway(Protocol):
    async def require_finalization_authority(self, *, batch_id: str) -> FinalizeBatchAuthority: ...

    async def lookup_stored(
        self, *, identity_scope: BatchFinalizationIdentityScope
    ) -> BatchFinalizationProjection | None: ...

    async def get_mutation_snapshot_for_update(
        self, *, batch_id: str
    ) -> BatchFinalizationMutationSnapshot: ...

    async def publish_finalization(
        self, *, publication: BatchFinalizationPublication
    ) -> ReplayResult[BatchFinalizationProjection]: ...


def _require_string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        _invalid(entity, field, "invalid")


def _require_nonnegative_int(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(entity, field, "invalid")


def _require_positive_int(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _invalid(entity, field, "invalid")


def _require_digest(entity: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity, field, "not_digest")


def _invalid(entity: str, field: str, reason: str) -> None:
    raise PortContractError(resource=entity, field=field, reason=reason)
