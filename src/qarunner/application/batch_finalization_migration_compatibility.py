"""Pure M0 compatibility decisions for Batch finalization migration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from qarunner.domain.batch import BatchState

COMPATIBILITY_EPOCH = "M0-STATE-V1"
STATE_MODEL_VERSION = 1
_TERMINALS = frozenset(
    {BatchState.SUCCEEDED, BatchState.FAILED, BatchState.PARTIAL, BatchState.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class FinalizationReadinessMigrationFact:
    ref: str
    batch_id: str
    source_batch_version: int
    compatibility_epoch: str
    state_model_version: int

    def __post_init__(self) -> None:
        for field in ("ref", "batch_id", "compatibility_epoch"):
            _string(field, getattr(self, field), required=True)
        _version("source_batch_version", self.source_batch_version, required=True)
        _version("state_model_version", self.state_model_version, required=True)


@dataclass(frozen=True, slots=True)
class FinalizationBasisMigrationFact:
    ref: str
    batch_id: str
    source_batch_version: int
    readiness_ref: str
    outcome: BatchState
    compatibility_epoch: str
    state_model_version: int

    def __post_init__(self) -> None:
        for field in ("ref", "batch_id", "readiness_ref", "compatibility_epoch"):
            _string(field, getattr(self, field), required=True)
        _version("source_batch_version", self.source_batch_version, required=True)
        _version("state_model_version", self.state_model_version, required=True)
        if self.outcome not in _TERMINALS:
            raise ValueError("outcome must be an execution terminal")


@dataclass(frozen=True, slots=True)
class FinalizationMigrationRecord:
    record_id: str
    batch_id: str
    current_batch_version: int
    legacy_terminal: str | None
    state_model_version: int | None
    compatibility_epoch: str | None
    v1_state: BatchState | None
    readiness_fact: FinalizationReadinessMigrationFact | None
    basis_fact: FinalizationBasisMigrationFact | None
    outcome: BatchState | None

    def __post_init__(self) -> None:
        _string("record_id", self.record_id, required=True)
        _string("batch_id", self.batch_id, required=True)
        _version("current_batch_version", self.current_batch_version, required=True)
        _string("legacy_terminal", self.legacy_terminal)
        _version("state_model_version", self.state_model_version)
        _string("compatibility_epoch", self.compatibility_epoch)
        if self.readiness_fact is not None and not isinstance(
            self.readiness_fact, FinalizationReadinessMigrationFact
        ):
            raise ValueError("readiness_fact must be typed or None")
        if self.basis_fact is not None and not isinstance(
            self.basis_fact, FinalizationBasisMigrationFact
        ):
            raise ValueError("basis_fact must be typed or None")
        if self.v1_state is not None and not isinstance(self.v1_state, BatchState):
            raise ValueError("v1_state must be a BatchState or None")
        if self.outcome is not None and self.outcome not in _TERMINALS:
            raise ValueError("outcome must be an execution terminal or None")


class FinalizationWriterRole(StrEnum):
    ATTEMPT_CREATOR = "attempt_creator"
    BATCH_RECONCILER = "batch_reconciler"
    LEGACY_BATCH_TERMINAL = "legacy_batch_terminal"


@dataclass(frozen=True, slots=True)
class FinalizationWriter:
    writer_id: str
    role: FinalizationWriterRole
    compatibility_epoch: str | None
    state_model_version: int | None

    def __post_init__(self) -> None:
        _string("writer_id", self.writer_id, required=True)
        if not isinstance(self.role, FinalizationWriterRole):
            raise ValueError("role must be a FinalizationWriterRole")
        _string("compatibility_epoch", self.compatibility_epoch)
        _version("state_model_version", self.state_model_version)

    @property
    def compatible(self) -> bool:
        return (
            self.compatibility_epoch == COMPATIBILITY_EPOCH
            and self.state_model_version == STATE_MODEL_VERSION
        )


@dataclass(frozen=True, slots=True)
class FinalizationMigrationControl:
    writers: tuple[FinalizationWriter, ...]
    committed_v1_fact_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.writers, tuple) or any(
            not isinstance(value, FinalizationWriter) for value in self.writers
        ):
            raise ValueError("writers must be a typed tuple")
        writer_ids = tuple(value.writer_id for value in self.writers)
        if len(set(writer_ids)) != len(writer_ids):
            raise ValueError("writers must not contain duplicate identity")
        _canonical_strings("committed_v1_fact_refs", self.committed_v1_fact_refs)


def classify_finalization_record(record: FinalizationMigrationRecord) -> tuple[str, str]:
    if record.compatibility_epoch is not None:
        if record.compatibility_epoch != COMPATIBILITY_EPOCH:
            return ("fail_closed", "unsupported_compatibility_epoch")
        if record.state_model_version != STATE_MODEL_VERSION:
            return ("fail_closed", "unsupported_state_model_version")
    elif record.state_model_version is not None:
        return ("fail_closed", "missing_compatibility_epoch")

    if record.v1_state is None:
        if any(
            value is not None
            for value in (record.readiness_fact, record.basis_fact, record.outcome)
        ):
            return ("conflict", "detached_v1_facts")
        if record.legacy_terminal is not None:
            return ("legacy_unverified", "basisless_legacy_terminal")
        return ("legacy_pending", "no_terminal_fact")
    if record.compatibility_epoch is None or record.state_model_version is None:
        return ("fail_closed", "missing_v1_compatibility_binding")
    if record.v1_state is BatchState.FINALIZING:
        if record.legacy_terminal is not None:
            return ("conflict", "legacy_terminal_with_finalizing")
        if (
            record.readiness_fact is None
            or record.basis_fact is not None
            or record.outcome is not None
        ):
            return ("conflict", "invalid_finalizing_facts")
        readiness = record.readiness_fact
        if (
            readiness.batch_id != record.batch_id
            or readiness.compatibility_epoch != record.compatibility_epoch
            or readiness.state_model_version != record.state_model_version
            or record.current_batch_version != readiness.source_batch_version + 1
        ):
            return ("conflict", "readiness_binding_mismatch")
        return ("trusted_v1", "readiness_fact_complete")
    if record.v1_state not in _TERMINALS:
        return ("fail_closed", "unsupported_v1_state")
    if record.readiness_fact is None or record.basis_fact is None or record.outcome is None:
        return ("conflict", "incomplete_terminal_facts")
    readiness = record.readiness_fact
    basis = record.basis_fact
    if (
        readiness.batch_id != record.batch_id
        or basis.batch_id != record.batch_id
        or basis.readiness_ref != readiness.ref
        or readiness.compatibility_epoch != record.compatibility_epoch
        or basis.compatibility_epoch != record.compatibility_epoch
        or readiness.state_model_version != record.state_model_version
        or basis.state_model_version != record.state_model_version
        or basis.source_batch_version != readiness.source_batch_version + 1
        or record.current_batch_version != basis.source_batch_version + 1
    ):
        return ("conflict", "terminal_fact_binding_mismatch")
    if record.outcome is not record.v1_state:
        return ("conflict", "terminal_outcome_mismatch")
    if basis.outcome is not record.outcome:
        return ("conflict", "basis_outcome_mismatch")
    if record.legacy_terminal is not None and record.legacy_terminal != record.outcome.value:
        return ("conflict", "legacy_v1_terminal_mismatch")
    return ("trusted_v1", "terminal_facts_complete")


def evaluate_finalization_activation(control: FinalizationMigrationControl) -> tuple[bool, str]:
    if any(
        value.role is FinalizationWriterRole.LEGACY_BATCH_TERMINAL for value in control.writers
    ):
        return (False, "legacy_terminal_writer_active")
    reconcilers = tuple(
        value for value in control.writers if value.role is FinalizationWriterRole.BATCH_RECONCILER
    )
    if len(reconcilers) != 1:
        return (False, "single_reconciler_required")
    if not reconcilers[0].compatible:
        return (False, "reconciler_epoch_mismatch")
    if any(
        not value.compatible
        for value in control.writers
        if value.role is FinalizationWriterRole.ATTEMPT_CREATOR
    ):
        return (False, "attempt_writer_epoch_mismatch")
    return (True, "compatible_writer_set")


def evaluate_finalization_rollback(control: FinalizationMigrationControl) -> tuple[bool, str]:
    if control.committed_v1_fact_refs:
        return (False, "forward_only_v1_facts")
    return (True, "precommit_rollback")


def _string(field: str, value: object, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _version(field: str, value: object, *, required: bool = False) -> None:
    if value is None and required:
        raise ValueError(f"{field} must be a non-negative integer")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{field} must be a non-negative integer or None")


def _canonical_strings(field: str, values: object) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        raise ValueError(f"{field} must be a tuple of non-empty strings")
    if values != tuple(sorted(values)) or len(set(values)) != len(values):
        raise ValueError(f"{field} must be canonical and unique")
