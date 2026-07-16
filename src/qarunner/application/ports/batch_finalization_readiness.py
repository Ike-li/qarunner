"""Authority and atomic publication contracts for entering Batch finalization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from qarunner.application.ports.common import PortContractError, ReplayResult
from qarunner.application.run_closed_handoff import RunClosedHandoff
from qarunner.domain.batch import BatchState
from qarunner.domain.batch_finalization import BatchSuccessPolicy
from qarunner.domain.digest import Digest, canonical_digest, canonical_materialized_run_set_digest
from qarunner.domain.manifest import BoundShardPlan
from qarunner.domain.retry import RetryIntent
from qarunner.domain.run_finalization import (
    RunDisposition,
    RunFinalizationBasis,
    RunFinalizationState,
    RunItemKey,
    RunItemResolutionSet,
    RunPhase,
)

READINESS_SCHEMA = "qep.batch-finalization-readiness.v1"
type BatchFinalizationReadinessIdentityScope = tuple[str, str, int]


class AttemptCreationOpportunityKind(StrEnum):
    INITIAL_COMMIT = "initial_commit"
    RETRY_COMMIT = "retry_commit"


class BatchFinalizationReadinessReason(StrEnum):
    READY = "ready"
    PENDING_RETRY = "pending_retry"
    PENDING_ATTEMPT = "pending_attempt"


@dataclass(frozen=True, slots=True)
class AttemptCreationOpportunity:
    run_id: str
    kind: AttemptCreationOpportunityKind
    authority_digest: Digest

    def __post_init__(self) -> None:
        _string("attempt_creation_opportunity", "run_id", self.run_id)
        if not isinstance(self.kind, AttemptCreationOpportunityKind):
            _invalid("attempt_creation_opportunity", "kind", "invalid")
        _digest("attempt_creation_opportunity", "authority_digest", self.authority_digest)


@dataclass(frozen=True, slots=True)
class BatchRunClosure:
    handoff: RunClosedHandoff
    basis: RunFinalizationBasis
    resolution_set: RunItemResolutionSet
    state: RunFinalizationState

    def __post_init__(self) -> None:
        entity = "batch_run_closure"
        if not isinstance(self.handoff, RunClosedHandoff):
            _invalid(entity, "handoff", "invalid")
        if not isinstance(self.basis, RunFinalizationBasis):
            _invalid(entity, "basis", "invalid")
        if not isinstance(self.resolution_set, RunItemResolutionSet):
            _invalid(entity, "resolution_set", "invalid")
        if not isinstance(self.state, RunFinalizationState):
            _invalid(entity, "state", "invalid")
        if (
            self.state.phase is not RunPhase.CLOSED
            or self.state.disposition is not RunDisposition.CLOSED_NO_RETRY
            or self.state.finalization_basis_digest != self.basis.basis_digest
            or self.state.outcome is not self.basis.outcome
            or self.handoff.batch_id != self.basis.batch_id
            or self.handoff.run_id != self.basis.run_id
            or self.handoff.source_run_version != self.basis.source_run_version
            or self.handoff.run_basis_digest != self.basis.basis_digest
            or self.handoff.manifest_digest != self.basis.manifest_digest
            or self.handoff.shard_plan_digest != self.basis.shard_plan_digest
            or self.handoff.item_resolution_set_digest != self.basis.item_resolution_set_digest
            or self.resolution_set.batch_id != self.basis.batch_id
            or self.resolution_set.run_id != self.basis.run_id
            or self.resolution_set.source_run_version != self.basis.source_run_version
            or self.resolution_set.resolution_set_digest != self.basis.item_resolution_set_digest
        ):
            _invalid(entity, "binding", "mismatch")

    @property
    def run_id(self) -> str:
        return self.basis.run_id


@dataclass(frozen=True, slots=True)
class FrozenBoundShardPlan:
    plan_version: int
    bound_plan: BoundShardPlan
    binding_digest: Digest

    def __post_init__(self) -> None:
        entity = "frozen_bound_shard_plan"
        _nonnegative(entity, "plan_version", self.plan_version)
        if not isinstance(self.bound_plan, BoundShardPlan):
            _invalid(entity, "bound_plan", "invalid")
        _digest(entity, "binding_digest", self.binding_digest)
        if self.binding_digest != self._computed_digest():
            _invalid(entity, "binding_digest", "mismatch")

    @classmethod
    def freeze(cls, *, plan_version: int, bound_plan: BoundShardPlan) -> FrozenBoundShardPlan:
        if not isinstance(bound_plan, BoundShardPlan):
            _invalid("frozen_bound_shard_plan", "bound_plan", "invalid")
        candidate = cls.__new__(cls)
        object.__setattr__(candidate, "plan_version", plan_version)
        object.__setattr__(candidate, "bound_plan", bound_plan)
        object.__setattr__(candidate, "binding_digest", candidate._computed_digest())
        candidate.__post_init__()
        return candidate

    def _computed_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.frozen-bound-shard-plan.v1",
            payload={
                "plan_id": self.bound_plan.plan.id,
                "plan_version": self.plan_version,
                "plan_digest": self.bound_plan.plan.digest.value,
                "bindings": [
                    {"run_id": value.run_id, "shard_index": value.shard_index}
                    for value in self.bound_plan.bindings
                ],
            },
        )


@dataclass(frozen=True, slots=True)
class BatchFinalizationReadinessSnapshot:
    batch_id: str
    source_batch_version: int
    manifest_id: str
    manifest_digest: Digest
    item_count: int
    shard_plan_id: str
    frozen_plan: FrozenBoundShardPlan
    canonical_run_ids: tuple[str, ...]
    canonical_run_set_digest: Digest
    success_policy: BatchSuccessPolicy
    batch_cancellation_intent_digest: Digest | None
    run_closures: tuple[BatchRunClosure, ...]
    pending_retry_intents: tuple[RetryIntent, ...]
    attempt_creation_opportunities: tuple[AttemptCreationOpportunity, ...]

    def __post_init__(self) -> None:
        entity = "batch_finalization_readiness_snapshot"
        for field in ("batch_id", "manifest_id", "shard_plan_id"):
            _string(entity, field, getattr(self, field))
        _nonnegative(entity, "source_batch_version", self.source_batch_version)
        _positive(entity, "item_count", self.item_count)
        for field in ("manifest_digest", "canonical_run_set_digest"):
            _digest(entity, field, getattr(self, field))
        _canonical_strings(entity, "canonical_run_ids", self.canonical_run_ids, nonempty=True)
        if not isinstance(self.frozen_plan, FrozenBoundShardPlan):
            _invalid(entity, "frozen_plan", "invalid")
        bound = self.frozen_plan.bound_plan
        if (
            bound.plan.id != self.shard_plan_id
            or bound.plan.batch_id != self.batch_id
            or bound.plan.digest != self.shard_plan_digest
            or bound.plan.manifest.id != self.manifest_id
            or bound.plan.manifest.digest != self.manifest_digest
            or bound.plan.manifest.item_count != self.item_count
            or tuple(sorted(value.run_id for value in bound.bindings)) != self.canonical_run_ids
        ):
            _invalid(entity, "frozen_plan", "binding_mismatch")
        if not isinstance(self.success_policy, BatchSuccessPolicy):
            _invalid(entity, "success_policy", "invalid")
        if self.batch_cancellation_intent_digest is not None:
            _digest(
                entity, "batch_cancellation_intent_digest", self.batch_cancellation_intent_digest
            )
        if self.canonical_run_set_digest != canonical_materialized_run_set_digest(
            batch_id=self.batch_id, run_ids=self.canonical_run_ids
        ):
            _invalid(entity, "canonical_run_ids", "digest_mismatch")
        if (
            not isinstance(self.run_closures, tuple)
            or any(not isinstance(value, BatchRunClosure) for value in self.run_closures)
            or tuple(value.run_id for value in self.run_closures) != self.canonical_run_ids
            or any(
                value.basis.batch_id != self.batch_id
                or value.basis.manifest_digest != self.manifest_digest
                or value.basis.shard_plan_digest != self.shard_plan_digest
                for value in self.run_closures
            )
        ):
            _invalid(entity, "run_closures", "binding_mismatch")
        bindings = {value.run_id: value.shard_index for value in bound.bindings}
        shards = {value.shard_index: value for value in bound.plan.shards}
        for closure in self.run_closures:
            expected_keys = tuple(
                RunItemKey(self.manifest_id, item_index)
                for item_index in shards[bindings[closure.run_id]].manifest_item_indices
            )
            actual_keys = tuple(value.item_key for value in closure.resolution_set.entries)
            if actual_keys != expected_keys:
                _invalid(entity, "run_closures", "shard_item_ownership_mismatch")
        if not isinstance(self.pending_retry_intents, tuple) or any(
            not isinstance(value, RetryIntent) for value in self.pending_retry_intents
        ):
            _invalid(entity, "pending_retry_intents", "invalid")
        retry_keys = tuple((value.run_id, value.id) for value in self.pending_retry_intents)
        if retry_keys != tuple(sorted(retry_keys)) or len(set(retry_keys)) != len(retry_keys):
            _invalid(entity, "pending_retry_intents", "not_canonical")
        if any(value.run_id not in self.canonical_run_ids for value in self.pending_retry_intents):
            _invalid(entity, "pending_retry_intents", "unknown_run")
        if not isinstance(self.attempt_creation_opportunities, tuple) or any(
            not isinstance(value, AttemptCreationOpportunity)
            for value in self.attempt_creation_opportunities
        ):
            _invalid(entity, "attempt_creation_opportunities", "invalid")
        opportunity_keys = tuple(
            (value.run_id, value.kind.value) for value in self.attempt_creation_opportunities
        )
        if opportunity_keys != tuple(sorted(opportunity_keys)) or len(
            set(opportunity_keys)
        ) != len(opportunity_keys):
            _invalid(entity, "attempt_creation_opportunities", "not_canonical")
        if any(
            value.run_id not in self.canonical_run_ids
            for value in self.attempt_creation_opportunities
        ):
            _invalid(entity, "attempt_creation_opportunities", "unknown_run")

    @property
    def reason(self) -> BatchFinalizationReadinessReason:
        if self.pending_retry_intents:
            return BatchFinalizationReadinessReason.PENDING_RETRY
        if self.attempt_creation_opportunities:
            return BatchFinalizationReadinessReason.PENDING_ATTEMPT
        return BatchFinalizationReadinessReason.READY

    @property
    def readiness_digest(self) -> Digest:
        return canonical_digest(
            schema_version=READINESS_SCHEMA,
            payload={
                "batch_id": self.batch_id,
                "source_batch_version": self.source_batch_version,
                "manifest_id": self.manifest_id,
                "manifest_digest": self.manifest_digest.value,
                "item_count": self.item_count,
                "shard_plan_id": self.shard_plan_id,
                "shard_plan_version": self.frozen_plan.plan_version,
                "shard_plan_digest": self.shard_plan_digest.value,
                "frozen_plan_binding_digest": self.frozen_plan.binding_digest.value,
                "canonical_run_ids": list(self.canonical_run_ids),
                "canonical_run_set_digest": self.canonical_run_set_digest.value,
                "success_policy_digest": self.success_policy.policy_digest.value,
                "batch_cancellation_intent_digest": _optional(
                    self.batch_cancellation_intent_digest
                ),
                "run_basis_digests": [
                    value.basis.basis_digest.value for value in self.run_closures
                ],
                "pending_retry_intents": [
                    {"run_id": value.run_id, "intent_digest": value.digest.value}
                    for value in self.pending_retry_intents
                ],
                "attempt_creation_opportunities": [
                    {
                        "run_id": value.run_id,
                        "kind": value.kind.value,
                        "authority_digest": value.authority_digest.value,
                    }
                    for value in self.attempt_creation_opportunities
                ],
            },
        )

    @property
    def shard_plan_version(self) -> int:
        return self.frozen_plan.plan_version

    @property
    def shard_plan_digest(self) -> Digest:
        return self.frozen_plan.bound_plan.plan.digest


@dataclass(frozen=True, slots=True)
class BatchFinalizationReadinessAuthority:
    snapshot: BatchFinalizationReadinessSnapshot
    authority_digest: Digest
    write_epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, BatchFinalizationReadinessSnapshot):
            _invalid("batch_finalization_readiness_authority", "snapshot", "invalid")
        _digest(
            "batch_finalization_readiness_authority", "authority_digest", self.authority_digest
        )
        _positive("batch_finalization_readiness_authority", "write_epoch", self.write_epoch)

    @property
    def identity_scope(self) -> BatchFinalizationReadinessIdentityScope:
        return (READINESS_SCHEMA, self.snapshot.batch_id, self.snapshot.source_batch_version)


@dataclass(frozen=True, slots=True)
class BatchFinalizationReadinessProjection:
    batch_id: str
    source_batch_version: int
    batch_version: int
    state: BatchState
    readiness_digest: Digest

    def __post_init__(self) -> None:
        _string("batch_finalization_readiness_projection", "batch_id", self.batch_id)
        _nonnegative(
            "batch_finalization_readiness_projection",
            "source_batch_version",
            self.source_batch_version,
        )
        _positive("batch_finalization_readiness_projection", "batch_version", self.batch_version)
        if self.state is not BatchState.FINALIZING:
            _invalid("batch_finalization_readiness_projection", "state", "not_finalizing")
        _digest(
            "batch_finalization_readiness_projection", "readiness_digest", self.readiness_digest
        )


@dataclass(frozen=True, slots=True)
class BatchFinalizationReadinessMutationSnapshot:
    batch_id: str
    batch_version: int
    state: BatchState
    readiness_snapshot: BatchFinalizationReadinessSnapshot

    def __post_init__(self) -> None:
        _string("batch_finalization_readiness_mutation", "batch_id", self.batch_id)
        _nonnegative("batch_finalization_readiness_mutation", "batch_version", self.batch_version)
        if self.state is not BatchState.RUNNING:
            _invalid("batch_finalization_readiness_mutation", "state", "not_running")
        if not isinstance(self.readiness_snapshot, BatchFinalizationReadinessSnapshot):
            _invalid("batch_finalization_readiness_mutation", "readiness_snapshot", "invalid")
        if (self.batch_id, self.batch_version) != (
            self.readiness_snapshot.batch_id,
            self.readiness_snapshot.source_batch_version,
        ):
            _invalid("batch_finalization_readiness_mutation", "binding", "mismatch")


@dataclass(frozen=True, slots=True)
class BatchFinalizationReadinessSideEffect:
    batch_id: str
    readiness_digest: Digest

    def __post_init__(self) -> None:
        _string("batch_finalization_readiness_side_effect", "batch_id", self.batch_id)
        _digest(
            "batch_finalization_readiness_side_effect",
            "readiness_digest",
            self.readiness_digest,
        )


@dataclass(frozen=True, slots=True)
class BatchFinalizationReadinessPublication:
    authority: BatchFinalizationReadinessAuthority
    expected_snapshot: BatchFinalizationReadinessMutationSnapshot
    projection: BatchFinalizationReadinessProjection
    side_effect: BatchFinalizationReadinessSideEffect

    def __post_init__(self) -> None:
        if (
            not isinstance(self.authority, BatchFinalizationReadinessAuthority)
            or not isinstance(self.expected_snapshot, BatchFinalizationReadinessMutationSnapshot)
            or not isinstance(self.projection, BatchFinalizationReadinessProjection)
            or not isinstance(self.side_effect, BatchFinalizationReadinessSideEffect)
            or self.authority.snapshot.reason is not BatchFinalizationReadinessReason.READY
            or self.expected_snapshot.readiness_snapshot != self.authority.snapshot
            or self.projection.batch_id != self.authority.snapshot.batch_id
            or self.projection.source_batch_version != self.authority.snapshot.source_batch_version
            or self.projection.batch_version != self.expected_snapshot.batch_version + 1
            or self.projection.readiness_digest != self.authority.snapshot.readiness_digest
            or self.side_effect.batch_id != self.projection.batch_id
            or self.side_effect.readiness_digest != self.projection.readiness_digest
        ):
            _invalid("batch_finalization_readiness_publication", "binding", "mismatch")

    @property
    def identity_scope(self) -> BatchFinalizationReadinessIdentityScope:
        return self.authority.identity_scope


@runtime_checkable
class BatchFinalizationReadinessGateway(Protocol):
    async def require_readiness_authority(
        self, *, batch_id: str
    ) -> BatchFinalizationReadinessAuthority: ...

    async def lookup_stored(
        self, *, identity_scope: BatchFinalizationReadinessIdentityScope
    ) -> BatchFinalizationReadinessProjection | None: ...

    async def get_mutation_snapshot_for_update(
        self, *, batch_id: str
    ) -> BatchFinalizationReadinessMutationSnapshot: ...

    async def publish_readiness(
        self, *, publication: BatchFinalizationReadinessPublication
    ) -> ReplayResult[BatchFinalizationReadinessProjection]: ...


def _string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        _invalid(entity, field, "invalid")


def _nonnegative(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(entity, field, "invalid")


def _positive(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _invalid(entity, field, "invalid")


def _digest(entity: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity, field, "invalid")


def _canonical_strings(entity: str, field: str, values: object, *, nonempty: bool) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        _invalid(entity, field, "invalid")
    if nonempty and not values:
        _invalid(entity, field, "empty")
    if values != tuple(sorted(values)) or len(set(values)) != len(values):
        _invalid(entity, field, "not_canonical")


def _optional(value: Digest | None) -> str | None:
    return None if value is None else value.value


def _invalid(entity: str, field: str, reason: str) -> None:
    raise PortContractError(resource=entity, field=field, reason=reason)
