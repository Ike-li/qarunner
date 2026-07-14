"""Batch aggregate for the greenfield execution lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import cast

from qarunner.domain.cancellation import (
    BatchCancellationIntent,
    BatchCancellationScopeKind,
)
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import (
    BatchCancellationConflict,
    DomainValidationError,
    IdempotencyConflict,
    InvalidTransition,
    ensure_expected_version,
)


class BatchState(enum.StrEnum):
    """States of one immutable Batch aggregate."""

    DRAFT = "draft"
    VALIDATING = "validating"
    COLLECTING = "collecting"
    PLANNING = "planning"
    AWAITING_ADMISSION = "awaiting_admission"
    QUEUED = "queued"
    RUNNING = "running"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class BatchRejectionStage(enum.StrEnum):
    """Persisted phase that produced a pre-execution rejection."""

    VALIDATION = "validation"
    COLLECTION = "collection"
    PLANNING = "planning"
    ADMISSION = "admission"


class BatchRejectionReasonClass(enum.StrEnum):
    """Stable machine categories for pre-execution failure facts."""

    INVALID_INPUT = "invalid_input"
    AUTHORIZATION_DENIED = "authorization_denied"
    POLICY_DENIED = "policy_denied"
    SOURCE_FAILURE = "source_failure"
    INTEGRITY_FAILURE = "integrity_failure"
    PLANNING_FAILURE = "planning_failure"
    CAPACITY_REJECTED = "capacity_rejected"


class BatchPreexecutionScopeKind(enum.StrEnum):
    """Known scope available before any Run has materialized."""

    PRE_PLAN = "pre_plan"
    PLANNED_UNMATERIALIZED = "planned_unmaterialized"


class BatchPreexecutionTerminalKind(enum.StrEnum):
    """Fact-aware terminal command represented by a closure basis."""

    REJECTION = "rejection"
    PRESTART_CANCEL = "prestart_cancel"


@dataclass(frozen=True, slots=True)
class BatchPreexecutionScopeItem:
    """Immutable neutral `qep.batch-preexecution-scope-item.v1` fact."""

    batch_id: str
    source_batch_version: int
    terminal_kind: BatchPreexecutionTerminalKind
    rejection_fact_digest: Digest | None
    batch_cancellation_intent_digest: Digest | None
    manifest_id: str
    manifest_digest: Digest
    manifest_item_key: str
    shard_plan_id: str
    shard_plan_version: int
    shard_plan_digest: Digest
    materialized_run_absence_digest: Digest
    resolution: str

    def __post_init__(self) -> None:
        entity_type = "batch_preexecution_scope_item"
        for field in ("batch_id", "manifest_id", "manifest_item_key", "shard_plan_id"):
            _require_nonempty_string(entity_type, field, getattr(self, field))
        _require_nonnegative_version(
            entity_type,
            "source_batch_version",
            self.source_batch_version,
        )
        _require_nonnegative_version(
            entity_type,
            "shard_plan_version",
            self.shard_plan_version,
        )
        if not isinstance(self.terminal_kind, BatchPreexecutionTerminalKind):
            _invalid(entity_type, "terminal_kind", "unknown")
        for field in (
            "manifest_digest",
            "shard_plan_digest",
            "materialized_run_absence_digest",
        ):
            _require_digest(entity_type, field, getattr(self, field))
        if self.terminal_kind is BatchPreexecutionTerminalKind.REJECTION:
            if self.rejection_fact_digest is None:
                _invalid(entity_type, "rejection_fact_digest", "required_for_terminal")
            _require_digest(entity_type, "rejection_fact_digest", self.rejection_fact_digest)
            if self.batch_cancellation_intent_digest is not None:
                _invalid(
                    entity_type,
                    "batch_cancellation_intent_digest",
                    "forbidden_for_terminal",
                )
        else:
            if self.batch_cancellation_intent_digest is None:
                _invalid(
                    entity_type,
                    "batch_cancellation_intent_digest",
                    "required_for_terminal",
                )
            _require_digest(
                entity_type,
                "batch_cancellation_intent_digest",
                self.batch_cancellation_intent_digest,
            )
            if self.rejection_fact_digest is not None:
                _invalid(
                    entity_type,
                    "rejection_fact_digest",
                    "forbidden_for_terminal",
                )
        if self.resolution != "not_started":
            _invalid(entity_type, "resolution", "unknown")

    @property
    def digest(self) -> Digest:
        """Bind command, item, Plan, and no-materialized-Run identity."""
        return canonical_digest(
            schema_version="qep.batch-preexecution-scope-item.v1",
            payload={
                "batch_id": self.batch_id,
                "source_batch_version": self.source_batch_version,
                "terminal_kind": self.terminal_kind.value,
                "rejection_fact_digest": _optional_digest_value(self.rejection_fact_digest),
                "batch_cancellation_intent_digest": _optional_digest_value(
                    self.batch_cancellation_intent_digest
                ),
                "manifest_id": self.manifest_id,
                "manifest_digest": self.manifest_digest.value,
                "manifest_item_key": self.manifest_item_key,
                "shard_plan_id": self.shard_plan_id,
                "shard_plan_version": self.shard_plan_version,
                "shard_plan_digest": self.shard_plan_digest.value,
                "materialized_run_absence_digest": (self.materialized_run_absence_digest.value),
                "resolution": self.resolution,
            },
        )


_ALLOWED_TRANSITIONS: dict[BatchState, frozenset[BatchState]] = {
    BatchState.DRAFT: frozenset({BatchState.VALIDATING}),
    BatchState.VALIDATING: frozenset({BatchState.COLLECTING}),
    BatchState.COLLECTING: frozenset({BatchState.PLANNING}),
    BatchState.PLANNING: frozenset({BatchState.AWAITING_ADMISSION}),
    BatchState.AWAITING_ADMISSION: frozenset({BatchState.QUEUED}),
    BatchState.QUEUED: frozenset({BatchState.RUNNING}),
    BatchState.RUNNING: frozenset({BatchState.FINALIZING}),
    # Terminal classification is deliberately not exposed through transition().
    # A later M0 slice adds a finalize command that enforces Run/Evidence facts.
    BatchState.FINALIZING: frozenset(),
    BatchState.SUCCEEDED: frozenset(),
    BatchState.FAILED: frozenset(),
    BatchState.PARTIAL: frozenset(),
    BatchState.CANCELLED: frozenset(),
    BatchState.REJECTED: frozenset(),
}

_BATCH_TERMINAL_STATES = frozenset(
    {
        BatchState.SUCCEEDED,
        BatchState.FAILED,
        BatchState.PARTIAL,
        BatchState.CANCELLED,
        BatchState.REJECTED,
    }
)

_BATCH_REJECTION_STAGE_BY_PHASE = {
    BatchState.VALIDATING: BatchRejectionStage.VALIDATION,
    BatchState.COLLECTING: BatchRejectionStage.COLLECTION,
    BatchState.PLANNING: BatchRejectionStage.PLANNING,
    BatchState.AWAITING_ADMISSION: BatchRejectionStage.ADMISSION,
}

_BATCH_PRESTART_CANCEL_STATES = frozenset(
    {
        BatchState.DRAFT,
        BatchState.VALIDATING,
        BatchState.COLLECTING,
        BatchState.PLANNING,
        BatchState.AWAITING_ADMISSION,
        BatchState.QUEUED,
    }
)

_BATCH_FROZEN_PLAN_CANCEL_STATES = frozenset(
    {
        BatchState.AWAITING_ADMISSION,
        BatchState.QUEUED,
    }
)


@dataclass(frozen=True, slots=True)
class BatchRejection:
    """Immutable `qep.batch-rejection.v1` fact."""

    rejection_id: str
    batch_id: str
    source_batch_version: int
    stage: BatchRejectionStage
    reason_class: BatchRejectionReasonClass
    reason_code: str
    input_digest: Digest
    authority_digest: Digest | None
    recorded_at: datetime

    def __post_init__(self) -> None:
        for field in ("rejection_id", "batch_id", "reason_code"):
            _require_nonempty_string("batch_rejection", field, getattr(self, field))
        _require_nonnegative_version(
            "batch_rejection",
            "source_batch_version",
            self.source_batch_version,
        )
        if not isinstance(self.stage, BatchRejectionStage):
            _invalid("batch_rejection", "stage", "unknown")
        if not isinstance(self.reason_class, BatchRejectionReasonClass):
            _invalid("batch_rejection", "reason_class", "unknown")
        _require_digest("batch_rejection", "input_digest", self.input_digest)
        if self.authority_digest is not None:
            _require_digest("batch_rejection", "authority_digest", self.authority_digest)
        if (
            self.stage is BatchRejectionStage.ADMISSION
            or self.reason_class
            in {
                BatchRejectionReasonClass.AUTHORIZATION_DENIED,
                BatchRejectionReasonClass.POLICY_DENIED,
            }
        ) and self.authority_digest is None:
            _invalid(
                "batch_rejection",
                "authority_digest",
                "required_for_rejection",
            )
        _require_utc("batch_rejection", "recorded_at", self.recorded_at)

    @property
    def digest(self) -> Digest:
        """Bind the complete rejection fact to its source Batch version."""
        return canonical_digest(
            schema_version="qep.batch-rejection.v1",
            payload={
                "rejection_id": self.rejection_id,
                "batch_id": self.batch_id,
                "source_batch_version": self.source_batch_version,
                "stage": self.stage.value,
                "reason_class": self.reason_class.value,
                "reason_code": self.reason_code,
                "input_digest": self.input_digest.value,
                "authority_digest": (
                    None if self.authority_digest is None else self.authority_digest.value
                ),
                "recorded_at": self.recorded_at.isoformat().replace("+00:00", "Z"),
            },
        )


@dataclass(frozen=True, slots=True)
class BatchPreexecutionSnapshot:
    """Trusted zero-Run scope and absence facts supplied to a Batch command."""

    batch_id: str
    source_batch_version: int
    scope_kind: BatchPreexecutionScopeKind
    submission_digest: Digest
    preplan_scope_digest: Digest | None
    manifest_digest: Digest | None
    shard_plan_version: int | None
    shard_plan_digest: Digest | None
    canonical_run_set_digest: Digest | None
    materialized_run_absence_digest: Digest
    execution_absence_snapshot_digest: Digest
    task_stop_fact_digests: tuple[Digest, ...]
    scope_items: tuple[BatchPreexecutionScopeItem, ...]
    item_coverage_proof_digest: Digest | None

    def __post_init__(self) -> None:
        entity_type = "batch_preexecution_snapshot"
        _require_nonempty_string(entity_type, "batch_id", self.batch_id)
        _require_nonnegative_version(
            entity_type,
            "source_batch_version",
            self.source_batch_version,
        )
        if not isinstance(self.scope_kind, BatchPreexecutionScopeKind):
            _invalid(entity_type, "scope_kind", "unknown")
        for field in (
            "submission_digest",
            "materialized_run_absence_digest",
            "execution_absence_snapshot_digest",
        ):
            _require_digest(entity_type, field, getattr(self, field))
        _require_digest_tuple_members(
            entity_type,
            "task_stop_fact_digests",
            self.task_stop_fact_digests,
        )
        if self.scope_kind is BatchPreexecutionScopeKind.PRE_PLAN:
            if self.preplan_scope_digest is None:
                _invalid(entity_type, "preplan_scope_digest", "required_for_scope")
            _require_digest(entity_type, "preplan_scope_digest", self.preplan_scope_digest)
            for field in (
                "manifest_digest",
                "shard_plan_version",
                "shard_plan_digest",
                "canonical_run_set_digest",
                "scope_items",
                "item_coverage_proof_digest",
            ):
                value = getattr(self, field)
                if value is not None and value != ():
                    _invalid(entity_type, field, "forbidden_for_scope")
            return
        if self.preplan_scope_digest is not None:
            _invalid(entity_type, "preplan_scope_digest", "forbidden_for_scope")
        for field in (
            "manifest_digest",
            "shard_plan_digest",
            "canonical_run_set_digest",
            "item_coverage_proof_digest",
        ):
            value = getattr(self, field)
            if value is None:
                _invalid(entity_type, field, "required_for_scope")
            _require_digest(entity_type, field, value)
        if self.shard_plan_version is None:
            _invalid(entity_type, "shard_plan_version", "required_for_scope")
        _require_nonnegative_version(
            entity_type,
            "shard_plan_version",
            self.shard_plan_version,
        )
        _require_scope_items(entity_type, self.scope_items)
        if not self.scope_items:
            _invalid(entity_type, "scope_items", "required_for_scope")
        _require_scope_item_bindings(self)


@dataclass(frozen=True, slots=True)
class BatchPreexecutionClosureBasis:
    """Immutable basis for a zero-materialized-Run Batch terminal."""

    batch_id: str
    source_batch_version: int
    source_phase: BatchState
    terminal_kind: BatchPreexecutionTerminalKind
    rejection_fact_digest: Digest | None
    batch_cancellation_intent_digest: Digest | None
    scope_kind: BatchPreexecutionScopeKind
    submission_digest: Digest
    preplan_scope_digest: Digest | None
    manifest_digest: Digest | None
    shard_plan_version: int | None
    shard_plan_digest: Digest | None
    canonical_run_set_digest: Digest | None
    materialized_run_absence_digest: Digest
    execution_absence_snapshot_digest: Digest
    task_stop_fact_digests: tuple[Digest, ...]
    preexecution_scope_item_fact_digests: tuple[Digest, ...]
    item_coverage_proof_digest: Digest | None
    batch_outcome: BatchState

    def __post_init__(self) -> None:
        entity_type = "batch_preexecution_closure_basis"
        _require_nonempty_string(entity_type, "batch_id", self.batch_id)
        _require_nonnegative_version(
            entity_type,
            "source_batch_version",
            self.source_batch_version,
        )
        if not isinstance(self.source_phase, BatchState):
            _invalid(entity_type, "source_phase", "unknown")
        if not isinstance(self.terminal_kind, BatchPreexecutionTerminalKind):
            _invalid(entity_type, "terminal_kind", "unknown")
        if not isinstance(self.batch_outcome, BatchState):
            _invalid(entity_type, "batch_outcome", "unknown")
        if self.terminal_kind is BatchPreexecutionTerminalKind.REJECTION:
            if self.rejection_fact_digest is None:
                _invalid(entity_type, "rejection_fact_digest", "required_for_terminal")
            _require_digest(entity_type, "rejection_fact_digest", self.rejection_fact_digest)
            if self.batch_cancellation_intent_digest is not None:
                _invalid(
                    entity_type,
                    "batch_cancellation_intent_digest",
                    "forbidden_for_terminal",
                )
            if self.source_phase not in {
                BatchState.VALIDATING,
                BatchState.COLLECTING,
                BatchState.PLANNING,
                BatchState.AWAITING_ADMISSION,
            }:
                _invalid(entity_type, "source_phase", "invalid_for_terminal")
            if self.batch_outcome is not BatchState.REJECTED:
                _invalid(entity_type, "batch_outcome", "invalid_for_terminal")
        else:
            if self.batch_cancellation_intent_digest is None:
                _invalid(
                    entity_type,
                    "batch_cancellation_intent_digest",
                    "required_for_terminal",
                )
            _require_digest(
                entity_type,
                "batch_cancellation_intent_digest",
                self.batch_cancellation_intent_digest,
            )
            if self.rejection_fact_digest is not None:
                _invalid(
                    entity_type,
                    "rejection_fact_digest",
                    "forbidden_for_terminal",
                )
            if self.source_phase not in {
                BatchState.DRAFT,
                BatchState.VALIDATING,
                BatchState.COLLECTING,
                BatchState.PLANNING,
                BatchState.AWAITING_ADMISSION,
                BatchState.QUEUED,
            }:
                _invalid(entity_type, "source_phase", "invalid_for_terminal")
            if self.batch_outcome is not BatchState.CANCELLED:
                _invalid(entity_type, "batch_outcome", "invalid_for_terminal")
        if not isinstance(self.scope_kind, BatchPreexecutionScopeKind):
            _invalid(entity_type, "scope_kind", "unknown")
        expected_scope_kind = (
            BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
            if self.source_phase in _BATCH_FROZEN_PLAN_CANCEL_STATES
            else BatchPreexecutionScopeKind.PRE_PLAN
        )
        if self.scope_kind is not expected_scope_kind:
            _invalid(entity_type, "scope_kind", "invalid_for_source_phase")
        for field in (
            "submission_digest",
            "materialized_run_absence_digest",
            "execution_absence_snapshot_digest",
        ):
            _require_digest(entity_type, field, getattr(self, field))
        _require_digest_tuple_members(
            entity_type,
            "task_stop_fact_digests",
            self.task_stop_fact_digests,
        )
        _require_digest_tuple_members(
            entity_type,
            "preexecution_scope_item_fact_digests",
            self.preexecution_scope_item_fact_digests,
        )
        if self.scope_kind is BatchPreexecutionScopeKind.PRE_PLAN:
            if self.preplan_scope_digest is None:
                _invalid(entity_type, "preplan_scope_digest", "required_for_scope")
            _require_digest(entity_type, "preplan_scope_digest", self.preplan_scope_digest)
            for field in (
                "manifest_digest",
                "shard_plan_version",
                "shard_plan_digest",
                "canonical_run_set_digest",
                "preexecution_scope_item_fact_digests",
                "item_coverage_proof_digest",
            ):
                value = getattr(self, field)
                if value is not None and value != ():
                    _invalid(entity_type, field, "forbidden_for_scope")
            return
        if self.preplan_scope_digest is not None:
            _invalid(entity_type, "preplan_scope_digest", "forbidden_for_scope")
        for field in (
            "manifest_digest",
            "shard_plan_digest",
            "canonical_run_set_digest",
            "item_coverage_proof_digest",
        ):
            value = getattr(self, field)
            if value is None:
                _invalid(entity_type, field, "required_for_scope")
            _require_digest(entity_type, field, value)
        if self.shard_plan_version is None:
            _invalid(entity_type, "shard_plan_version", "required_for_scope")
        _require_nonnegative_version(
            entity_type,
            "shard_plan_version",
            self.shard_plan_version,
        )
        if not self.preexecution_scope_item_fact_digests:
            _invalid(
                entity_type,
                "preexecution_scope_item_fact_digests",
                "required_for_scope",
            )

    @property
    def digest(self) -> Digest:
        """Bind the complete command, scope, no-execution proof, and outcome."""
        return canonical_digest(
            schema_version="qep.batch-preexecution-closure-basis.v1",
            payload={
                "batch_id": self.batch_id,
                "source_batch_version": self.source_batch_version,
                "source_phase": self.source_phase.value,
                "terminal_kind": self.terminal_kind.value,
                "rejection_fact_digest": _optional_digest_value(self.rejection_fact_digest),
                "batch_cancellation_intent_digest": _optional_digest_value(
                    self.batch_cancellation_intent_digest
                ),
                "scope_kind": self.scope_kind.value,
                "submission_digest": self.submission_digest.value,
                "preplan_scope_digest": _optional_digest_value(self.preplan_scope_digest),
                "manifest_digest": _optional_digest_value(self.manifest_digest),
                "shard_plan_version": self.shard_plan_version,
                "shard_plan_digest": _optional_digest_value(self.shard_plan_digest),
                "canonical_run_set_digest": _optional_digest_value(self.canonical_run_set_digest),
                "materialized_run_absence_digest": (self.materialized_run_absence_digest.value),
                "execution_absence_snapshot_digest": (
                    self.execution_absence_snapshot_digest.value
                ),
                "task_stop_fact_digest": [digest.value for digest in self.task_stop_fact_digests],
                "preexecution_scope_item_fact_digest": [
                    digest.value for digest in self.preexecution_scope_item_fact_digests
                ],
                "item_coverage_proof_digest": _optional_digest_value(
                    self.item_coverage_proof_digest
                ),
                "batch_outcome": self.batch_outcome.value,
            },
        )


@dataclass(frozen=True, slots=True)
class Batch:
    """Immutable Batch state; successful commands return a new version."""

    id: str
    state: BatchState
    version: int
    rejection_fact: BatchRejection | None = None
    cancellation_intent: BatchCancellationIntent | None = None
    preexecution_closure_basis: BatchPreexecutionClosureBasis | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise DomainValidationError(
                entity_type="batch",
                field="id",
                reason="invalid",
            )
        if not isinstance(self.state, BatchState):
            raise DomainValidationError(
                entity_type="batch",
                field="state",
                reason="unknown",
            )
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise DomainValidationError(
                entity_type="batch",
                field="version",
                reason="invalid",
            )
        if self.rejection_fact is not None and not isinstance(self.rejection_fact, BatchRejection):
            _invalid("batch", "rejection_fact", "invalid_type")
        if self.cancellation_intent is not None and not isinstance(
            self.cancellation_intent, BatchCancellationIntent
        ):
            _invalid("batch", "cancellation_intent", "invalid_type")
        if self.preexecution_closure_basis is not None and not isinstance(
            self.preexecution_closure_basis,
            BatchPreexecutionClosureBasis,
        ):
            _invalid("batch", "preexecution_closure_basis", "invalid_type")
        basis = self.preexecution_closure_basis
        if basis is None:
            if self.rejection_fact is not None:
                _invalid("batch", "preexecution_closure_basis", "required_for_rejection")
            if self.cancellation_intent is not None:
                _require_pending_cancel_binding(
                    batch_id=self.id,
                    state=self.state,
                    version=self.version,
                    intent=self.cancellation_intent,
                )
            return
        if (
            basis.batch_id != self.id
            or basis.batch_outcome is not self.state
            or basis.source_batch_version != self.version - 1
        ):
            _invalid("batch", "preexecution_closure_basis", "aggregate_mismatch")
        if basis.terminal_kind is BatchPreexecutionTerminalKind.REJECTION:
            if (
                self.rejection_fact is None
                or self.cancellation_intent is not None
                or self.rejection_fact.batch_id != self.id
                or self.rejection_fact.source_batch_version != basis.source_batch_version
                or basis.rejection_fact_digest != self.rejection_fact.digest
            ):
                _invalid("batch", "rejection_fact", "basis_mismatch")
            if (
                self.rejection_fact.stage
                is not _BATCH_REJECTION_STAGE_BY_PHASE[basis.source_phase]
            ):
                _invalid("batch_rejection", "stage", "source_phase_mismatch")
            return
        if (
            self.cancellation_intent is None
            or self.rejection_fact is not None
            or self.cancellation_intent.batch_id != self.id
            or self.cancellation_intent.source_batch_version != basis.source_batch_version - 1
            or basis.batch_cancellation_intent_digest != self.cancellation_intent.digest
        ):
            _invalid("batch", "cancellation_intent", "basis_mismatch")
        _require_cancel_basis_binding(self.cancellation_intent, basis)

    @classmethod
    def create(cls, *, batch_id: str) -> Batch:
        """Create a Batch at the initial draft state."""
        return cls(id=batch_id, state=BatchState.DRAFT, version=0)

    def transition(self, target: BatchState, *, expected_version: int) -> Batch:
        """Return the next Batch version or reject an illegal state edge."""
        ensure_expected_version(
            entity_type="batch",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if not isinstance(target, BatchState):
            raise DomainValidationError(
                entity_type="batch",
                field="state",
                reason="unknown",
            )
        if self.cancellation_intent is not None:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=target,
                current_version=self.version,
                expected_version=expected_version,
            )
        return replace(self, state=target, version=self.version + 1)

    def request_cancel(
        self,
        *,
        intent: BatchCancellationIntent,
        expected_version: int,
    ) -> Batch:
        """Record one immutable Batch cancellation intent without claiming an outcome."""
        if self.cancellation_intent is not None:
            if self.cancellation_intent.idempotency_key == intent.idempotency_key:
                if self.cancellation_intent.request_digest != intent.request_digest:
                    raise IdempotencyConflict(
                        scope=f"batch:{self.id}:cancel",
                        key=intent.idempotency_key,
                        stored_digest=self.cancellation_intent.request_digest,
                        received_digest=intent.request_digest,
                    )
                return self
            raise BatchCancellationConflict(
                batch_id=self.id,
                stored_key=self.cancellation_intent.idempotency_key,
                received_key=intent.idempotency_key,
            )
        if self.state in _BATCH_TERMINAL_STATES:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.CANCELLED,
                current_version=self.version,
                expected_version=expected_version,
            )
        ensure_expected_version(
            entity_type="batch",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if self.state not in _BATCH_PRESTART_CANCEL_STATES:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.CANCELLED,
                current_version=self.version,
                expected_version=expected_version,
            )
        if intent.batch_id != self.id or intent.source_batch_version != self.version:
            raise DomainValidationError(
                entity_type="batch_cancellation_intent",
                field="source_batch",
                reason="mismatch",
            )
        expected_scope_kind = _expected_cancel_scope_kind(self.state)
        if intent.scope.kind is not expected_scope_kind:
            raise DomainValidationError(
                entity_type="batch_cancellation_intent",
                field="scope",
                reason="source_phase_mismatch",
            )
        return replace(
            self,
            version=self.version + 1,
            cancellation_intent=intent,
        )

    def reject_preexecution(
        self,
        *,
        rejection: BatchRejection,
        snapshot: BatchPreexecutionSnapshot,
        expected_version: int,
    ) -> Batch:
        """Atomically persist a matching rejection fact, basis, and terminal."""
        if (
            self.rejection_fact is not None
            and self.rejection_fact.rejection_id == rejection.rejection_id
        ):
            if self.rejection_fact.digest != rejection.digest:
                raise IdempotencyConflict(
                    scope=f"batch:{self.id}:rejection",
                    key=rejection.rejection_id,
                    stored_digest=self.rejection_fact.digest,
                    received_digest=rejection.digest,
                )
            stored_basis = cast(
                BatchPreexecutionClosureBasis,
                self.preexecution_closure_basis,
            )
            _require_snapshot_command_binding(
                snapshot,
                terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
                command_digest=rejection.digest,
            )
            candidate = _build_preexecution_basis(
                source_phase=stored_basis.source_phase,
                terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
                rejection_fact_digest=rejection.digest,
                batch_cancellation_intent_digest=None,
                snapshot=snapshot,
                batch_outcome=BatchState.REJECTED,
            )
            if self.state is BatchState.REJECTED and stored_basis.digest == candidate.digest:
                return self
            raise IdempotencyConflict(
                scope=f"batch:{self.id}:rejection",
                key=rejection.rejection_id,
                stored_digest=stored_basis.digest,
                received_digest=candidate.digest,
            )
        if self.state in _BATCH_TERMINAL_STATES:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.REJECTED,
                current_version=self.version,
                expected_version=expected_version,
            )
        ensure_expected_version(
            entity_type="batch",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if self.cancellation_intent is not None:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.REJECTED,
                current_version=self.version,
                expected_version=expected_version,
            )
        expected_stage = _BATCH_REJECTION_STAGE_BY_PHASE.get(self.state)
        if expected_stage is None:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.REJECTED,
                current_version=self.version,
                expected_version=expected_version,
            )
        if rejection.stage is not expected_stage:
            raise DomainValidationError(
                entity_type="batch_rejection",
                field="stage",
                reason="source_phase_mismatch",
            )
        if (
            rejection.batch_id != self.id
            or snapshot.batch_id != self.id
            or rejection.source_batch_version != self.version
            or snapshot.source_batch_version != self.version
        ):
            raise DomainValidationError(
                entity_type="batch_rejection",
                field="source_batch",
                reason="mismatch",
            )
        expected_scope_kind = (
            BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
            if self.state is BatchState.AWAITING_ADMISSION
            else BatchPreexecutionScopeKind.PRE_PLAN
        )
        if snapshot.scope_kind is not expected_scope_kind:
            raise DomainValidationError(
                entity_type="batch_preexecution_snapshot",
                field="scope_kind",
                reason="source_phase_mismatch",
            )
        _require_snapshot_command_binding(
            snapshot,
            terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
            command_digest=rejection.digest,
        )
        basis = _build_preexecution_basis(
            source_phase=self.state,
            terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
            rejection_fact_digest=rejection.digest,
            batch_cancellation_intent_digest=None,
            snapshot=snapshot,
            batch_outcome=BatchState.REJECTED,
        )
        return replace(
            self,
            state=BatchState.REJECTED,
            version=self.version + 1,
            rejection_fact=rejection,
            preexecution_closure_basis=basis,
        )

    def finalize_unmaterialized_cancel(
        self,
        *,
        snapshot: BatchPreexecutionSnapshot,
        expected_version: int,
    ) -> Batch:
        """Close a cancelled Batch only from its intent and zero-Run proof."""
        if (
            self.cancellation_intent is not None
            and self.preexecution_closure_basis is not None
            and self.preexecution_closure_basis.terminal_kind
            is BatchPreexecutionTerminalKind.PRESTART_CANCEL
        ):
            stored_basis = self.preexecution_closure_basis
            _require_snapshot_command_binding(
                snapshot,
                terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
                command_digest=self.cancellation_intent.digest,
            )
            candidate = _build_preexecution_basis(
                source_phase=stored_basis.source_phase,
                terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
                rejection_fact_digest=None,
                batch_cancellation_intent_digest=self.cancellation_intent.digest,
                snapshot=snapshot,
                batch_outcome=BatchState.CANCELLED,
            )
            if self.state is BatchState.CANCELLED and stored_basis.digest == candidate.digest:
                return self
            raise IdempotencyConflict(
                scope=f"batch:{self.id}:preexecution-cancel",
                key=self.cancellation_intent.idempotency_key,
                stored_digest=stored_basis.digest,
                received_digest=candidate.digest,
            )
        if self.state in _BATCH_TERMINAL_STATES:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.CANCELLED,
                current_version=self.version,
                expected_version=expected_version,
            )
        ensure_expected_version(
            entity_type="batch",
            entity_id=self.id,
            current_version=self.version,
            expected_version=expected_version,
        )
        if self.state not in _BATCH_PRESTART_CANCEL_STATES or self.cancellation_intent is None:
            raise InvalidTransition(
                entity_type="batch",
                entity_id=self.id,
                current_state=self.state,
                requested_state=BatchState.CANCELLED,
                current_version=self.version,
                expected_version=expected_version,
            )
        if snapshot.batch_id != self.id or snapshot.source_batch_version != self.version:
            raise DomainValidationError(
                entity_type="batch_preexecution_snapshot",
                field="source_batch",
                reason="mismatch",
            )
        _require_cancel_scope_binding(self.cancellation_intent, snapshot)
        _require_snapshot_command_binding(
            snapshot,
            terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
            command_digest=self.cancellation_intent.digest,
        )
        basis = _build_preexecution_basis(
            source_phase=self.state,
            terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
            rejection_fact_digest=None,
            batch_cancellation_intent_digest=self.cancellation_intent.digest,
            snapshot=snapshot,
            batch_outcome=BatchState.CANCELLED,
        )
        return replace(
            self,
            state=BatchState.CANCELLED,
            version=self.version + 1,
            preexecution_closure_basis=basis,
        )


def _build_preexecution_basis(
    *,
    source_phase: BatchState,
    terminal_kind: BatchPreexecutionTerminalKind,
    rejection_fact_digest: Digest | None,
    batch_cancellation_intent_digest: Digest | None,
    snapshot: BatchPreexecutionSnapshot,
    batch_outcome: BatchState,
) -> BatchPreexecutionClosureBasis:
    return BatchPreexecutionClosureBasis(
        batch_id=snapshot.batch_id,
        source_batch_version=snapshot.source_batch_version,
        source_phase=source_phase,
        terminal_kind=terminal_kind,
        rejection_fact_digest=rejection_fact_digest,
        batch_cancellation_intent_digest=batch_cancellation_intent_digest,
        scope_kind=snapshot.scope_kind,
        submission_digest=snapshot.submission_digest,
        preplan_scope_digest=snapshot.preplan_scope_digest,
        manifest_digest=snapshot.manifest_digest,
        shard_plan_version=snapshot.shard_plan_version,
        shard_plan_digest=snapshot.shard_plan_digest,
        canonical_run_set_digest=snapshot.canonical_run_set_digest,
        materialized_run_absence_digest=snapshot.materialized_run_absence_digest,
        execution_absence_snapshot_digest=snapshot.execution_absence_snapshot_digest,
        task_stop_fact_digests=snapshot.task_stop_fact_digests,
        preexecution_scope_item_fact_digests=tuple(item.digest for item in snapshot.scope_items),
        item_coverage_proof_digest=snapshot.item_coverage_proof_digest,
        batch_outcome=batch_outcome,
    )


def _require_snapshot_command_binding(
    snapshot: BatchPreexecutionSnapshot,
    *,
    terminal_kind: BatchPreexecutionTerminalKind,
    command_digest: Digest,
) -> None:
    for item in snapshot.scope_items:
        matches = item.terminal_kind is terminal_kind
        if terminal_kind is BatchPreexecutionTerminalKind.REJECTION:
            matches = matches and item.rejection_fact_digest == command_digest
        else:
            matches = matches and item.batch_cancellation_intent_digest == command_digest
        if not matches:
            _invalid(
                "batch_preexecution_snapshot",
                "scope_items",
                "command_binding_mismatch",
            )


def _require_pending_cancel_binding(
    *,
    batch_id: str,
    state: BatchState,
    version: int,
    intent: BatchCancellationIntent,
) -> None:
    if (
        intent.batch_id != batch_id
        or state not in _BATCH_PRESTART_CANCEL_STATES
        or intent.source_batch_version + 1 != version
    ):
        _invalid("batch", "cancellation_intent", "aggregate_mismatch")
    if intent.scope.kind is not _expected_cancel_scope_kind(state):
        _invalid(
            "batch_cancellation_intent",
            "scope",
            "source_phase_mismatch",
        )


def _expected_cancel_scope_kind(state: BatchState) -> BatchCancellationScopeKind:
    return (
        BatchCancellationScopeKind.FROZEN_PLAN
        if state in _BATCH_FROZEN_PLAN_CANCEL_STATES
        else BatchCancellationScopeKind.PRE_PLAN
    )


def _require_cancel_basis_binding(
    intent: BatchCancellationIntent,
    basis: BatchPreexecutionClosureBasis,
) -> None:
    scope = intent.scope
    if scope.kind is BatchCancellationScopeKind.PRE_PLAN:
        matches = (
            basis.scope_kind is BatchPreexecutionScopeKind.PRE_PLAN
            and basis.preplan_scope_digest == scope.preplan_scope_digest
        )
    else:
        matches = (
            basis.scope_kind is BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
            and basis.manifest_digest == scope.manifest_digest
            and basis.shard_plan_version == scope.shard_plan_version
            and basis.shard_plan_digest == scope.shard_plan_digest
            and basis.canonical_run_set_digest == scope.canonical_run_set_digest
        )
    if not matches:
        _invalid(
            "batch_preexecution_closure_basis",
            "scope",
            "cancellation_intent_mismatch",
        )


def _require_cancel_scope_binding(
    intent: BatchCancellationIntent,
    snapshot: BatchPreexecutionSnapshot,
) -> None:
    scope = intent.scope
    if scope.kind is BatchCancellationScopeKind.PRE_PLAN:
        matches = (
            snapshot.scope_kind is BatchPreexecutionScopeKind.PRE_PLAN
            and snapshot.preplan_scope_digest == scope.preplan_scope_digest
        )
    else:
        matches = (
            snapshot.scope_kind is BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
            and snapshot.manifest_digest == scope.manifest_digest
            and snapshot.shard_plan_version == scope.shard_plan_version
            and snapshot.shard_plan_digest == scope.shard_plan_digest
            and snapshot.canonical_run_set_digest == scope.canonical_run_set_digest
        )
    if not matches:
        _invalid(
            "batch_preexecution_snapshot",
            "scope",
            "cancellation_intent_mismatch",
        )


def _optional_digest_value(digest: Digest | None) -> str | None:
    return None if digest is None else digest.value


def _require_nonempty_string(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, str):
        _invalid(entity_type, field, "not_string")
    if not value.strip():
        _invalid(entity_type, field, "empty")


def _require_nonnegative_version(entity_type: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(entity_type, field, "not_integer")
    if value < 0:
        _invalid(entity_type, field, "negative")


def _require_digest(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity_type, field, "not_digest")


def _require_digest_tuple_members(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, tuple):
        _invalid(entity_type, field, "not_tuple")
    if any(not isinstance(item, Digest) for item in value):
        _invalid(entity_type, field, "not_digest")
    if len(value) != len(set(value)):
        _invalid(entity_type, field, "duplicate")


def _require_scope_items(entity_type: str, value: object) -> None:
    if not isinstance(value, tuple):
        _invalid(entity_type, "scope_items", "not_tuple")
    if any(not isinstance(item, BatchPreexecutionScopeItem) for item in value):
        _invalid(entity_type, "scope_items", "invalid_type")
    keys = tuple(item.manifest_item_key for item in value)
    if len(keys) != len(set(keys)):
        _invalid(entity_type, "scope_items", "duplicate")
    if keys != tuple(sorted(keys)):
        _invalid(entity_type, "scope_items", "not_canonical")


def _require_scope_item_bindings(snapshot: BatchPreexecutionSnapshot) -> None:
    first = snapshot.scope_items[0]
    shared = (
        first.terminal_kind,
        first.rejection_fact_digest,
        first.batch_cancellation_intent_digest,
        first.manifest_id,
        first.manifest_digest,
        first.shard_plan_id,
        first.shard_plan_version,
        first.shard_plan_digest,
        first.materialized_run_absence_digest,
    )
    for item in snapshot.scope_items:
        if (
            item.batch_id != snapshot.batch_id
            or item.source_batch_version != snapshot.source_batch_version
            or item.manifest_digest != snapshot.manifest_digest
            or item.shard_plan_version != snapshot.shard_plan_version
            or item.shard_plan_digest != snapshot.shard_plan_digest
            or item.materialized_run_absence_digest != snapshot.materialized_run_absence_digest
            or (
                item.terminal_kind,
                item.rejection_fact_digest,
                item.batch_cancellation_intent_digest,
                item.manifest_id,
                item.manifest_digest,
                item.shard_plan_id,
                item.shard_plan_version,
                item.shard_plan_digest,
                item.materialized_run_absence_digest,
            )
            != shared
        ):
            _invalid("batch_preexecution_snapshot", "scope_items", "binding_mismatch")


def _require_utc(entity_type: str, field: str, value: object) -> None:
    if not isinstance(value, datetime):
        _invalid(entity_type, field, "not_datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _invalid(entity_type, field, "not_utc")


def _invalid(entity_type: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity_type, field=field, reason=reason)
