"""Narrow authority and atomic publication port for Run finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.run_closed_handoff import RunClosedHandoff, build_run_closed_handoff
from qarunner.domain.digest import Digest
from qarunner.domain.run_finalization import (
    RunDisposition,
    RunFinalizationBasis,
    RunFinalizationState,
    RunItemResolutionSet,
    RunOutcome,
    RunPhase,
)

RUN_FINALIZATION_BASIS_SCHEMA = "qep.run-finalization-basis.v1"
type RunFinalizationIdentityScope = tuple[str, str, int]


@dataclass(frozen=True, slots=True)
class FinalizeRunAuthority:
    """Current single-writer authority for one source Run version."""

    run_id: str
    batch_id: str
    source_run_version: int
    authority_digest: Digest
    write_epoch: int

    def __post_init__(self) -> None:
        _require_string("finalize_run_authority", "run_id", self.run_id)
        _require_string("finalize_run_authority", "batch_id", self.batch_id)
        _require_nonnegative_int(
            "finalize_run_authority", "source_run_version", self.source_run_version
        )
        _require_digest("finalize_run_authority", "authority_digest", self.authority_digest)
        _require_positive_int("finalize_run_authority", "write_epoch", self.write_epoch)

    @property
    def identity_scope(self) -> RunFinalizationIdentityScope:
        return (RUN_FINALIZATION_BASIS_SCHEMA, self.run_id, self.source_run_version)


@dataclass(frozen=True, slots=True)
class RunFinalizationProjection:
    """Persisted terminal projection compared independently from its basis digest."""

    run_id: str
    source_run_version: int
    state: RunFinalizationState

    def __post_init__(self) -> None:
        entity = "run_finalization_projection"
        _require_string(entity, "run_id", self.run_id)
        _require_nonnegative_int(entity, "source_run_version", self.source_run_version)
        if not isinstance(self.state, RunFinalizationState):
            _invalid(entity, "state", "invalid")
        if (
            self.state.phase is not RunPhase.CLOSED
            or self.state.disposition is not RunDisposition.CLOSED_NO_RETRY
            or self.state.outcome is None
            or self.state.finalization_basis_digest is None
        ):
            _invalid(entity, "state", "not_closed")

    @property
    def basis_digest(self) -> Digest:
        assert self.state.finalization_basis_digest is not None
        return self.state.finalization_basis_digest


@dataclass(frozen=True, slots=True)
class RunFinalizationMutationSnapshot:
    """Locked mutable authority versions used only after stored replay lookup misses."""

    run_id: str
    run_version: int
    attempt_id: str | None
    attempt_version: int | None

    def __post_init__(self) -> None:
        entity = "run_finalization_mutation_snapshot"
        _require_string(entity, "run_id", self.run_id)
        _require_nonnegative_int(entity, "run_version", self.run_version)
        if (self.attempt_id is None) != (self.attempt_version is None):
            _invalid(entity, "attempt", "identity_all_or_none")
        if self.attempt_id is not None:
            _require_string(entity, "attempt_id", self.attempt_id)
            _require_nonnegative_int(entity, "attempt_version", self.attempt_version)


@dataclass(frozen=True, slots=True)
class RunFinalizationSideEffect:
    """Minimal semantic identity shared by terminal audit and outbox publication."""

    run_id: str
    basis_digest: Digest
    outcome: RunOutcome

    def __post_init__(self) -> None:
        entity = "run_finalization_side_effect"
        _require_string(entity, "run_id", self.run_id)
        _require_digest(entity, "basis_digest", self.basis_digest)
        if not isinstance(self.outcome, RunOutcome):
            _invalid(entity, "outcome", "unknown")


@dataclass(frozen=True, slots=True)
class RunFinalizationPublication:
    """One atomic terminal projection, basis identity, audit, and outbox publication."""

    authority: FinalizeRunAuthority
    expected_snapshot: RunFinalizationMutationSnapshot
    basis: RunFinalizationBasis
    resolution_set: RunItemResolutionSet
    projection: RunFinalizationProjection
    side_effect: RunFinalizationSideEffect
    handoff: RunClosedHandoff

    def __post_init__(self) -> None:
        entity = "run_finalization_publication"
        if not isinstance(self.authority, FinalizeRunAuthority):
            _invalid(entity, "authority", "invalid")
        if not isinstance(self.expected_snapshot, RunFinalizationMutationSnapshot):
            _invalid(entity, "expected_snapshot", "invalid")
        if not isinstance(self.basis, RunFinalizationBasis):
            _invalid(entity, "basis", "invalid")
        if not isinstance(self.resolution_set, RunItemResolutionSet):
            _invalid(entity, "resolution_set", "invalid")
        if not isinstance(self.projection, RunFinalizationProjection):
            _invalid(entity, "projection", "invalid")
        if not isinstance(self.side_effect, RunFinalizationSideEffect):
            _invalid(entity, "side_effect", "invalid")
        if not isinstance(self.handoff, RunClosedHandoff):
            _invalid(entity, "handoff", "invalid")
        if (
            self.authority.run_id != self.projection.run_id
            or self.authority.source_run_version != self.projection.source_run_version
            or self.expected_snapshot.run_id != self.projection.run_id
            or self.expected_snapshot.run_version != self.authority.source_run_version
            or self.expected_snapshot.attempt_id != self.basis.final_attempt_id
            or self.expected_snapshot.attempt_version != self.basis.final_attempt_version
            or self.basis.run_id != self.projection.run_id
            or self.basis.batch_id != self.authority.batch_id
            or self.basis.source_run_version != self.authority.source_run_version
            or self.basis.basis_digest != self.projection.basis_digest
            or self.resolution_set.run_id != self.projection.run_id
            or self.resolution_set.source_run_version != self.authority.source_run_version
            or self.resolution_set.resolution_set_digest != self.basis.item_resolution_set_digest
            or self.side_effect.run_id != self.projection.run_id
            or self.side_effect.basis_digest != self.basis.basis_digest
            or self.side_effect.outcome is not self.projection.state.outcome
            or self.handoff
            != build_run_closed_handoff(
                basis=self.basis,
                resolution_set=self.resolution_set,
                authority_digest=self.authority.authority_digest,
                write_epoch=self.authority.write_epoch,
            )
        ):
            _invalid(entity, "binding", "mismatch")

    @property
    def identity_scope(self) -> RunFinalizationIdentityScope:
        return self.authority.identity_scope


@runtime_checkable
class RunFinalizationGateway(Protocol):
    """Authorize, lock, and atomically publish one immutable Run terminal basis."""

    async def require_finalization_authority(self, *, run_id: str) -> FinalizeRunAuthority: ...

    async def lookup_stored(
        self, *, identity_scope: RunFinalizationIdentityScope
    ) -> RunFinalizationProjection | None: ...

    async def get_mutation_snapshot_for_update(
        self, *, run_id: str
    ) -> RunFinalizationMutationSnapshot: ...

    async def publish_finalization(
        self, *, publication: RunFinalizationPublication
    ) -> ReplayResult[RunFinalizationProjection]: ...


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
