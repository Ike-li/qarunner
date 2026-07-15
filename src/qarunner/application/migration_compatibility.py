"""Pure M0 compatibility decisions for Batch pre-execution migration.

This module deliberately owns no persistence or runtime integration.  It turns
already-read immutable facts and migration-control state into deterministic
decisions that adapters can enforce later.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPATIBILITY_EPOCH = "M0-STATE-V1"
STATE_MODEL_VERSION = 1


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    """Compatibility projection of one legacy/v1 Batch terminal record."""

    record_id: str
    legacy_terminal: str | None
    state_model_version: int | None
    compatibility_epoch: str | None
    ownership_digest: str | None
    command_digest: str | None
    basis_digest: str | None
    proof_digest: str | None
    v1_terminal: str | None

    def __post_init__(self) -> None:
        _require_optional_string("record_id", self.record_id, required=True)
        _require_optional_string("legacy_terminal", self.legacy_terminal)
        _require_optional_string("compatibility_epoch", self.compatibility_epoch)
        for field in (
            "ownership_digest",
            "command_digest",
            "basis_digest",
            "proof_digest",
            "v1_terminal",
        ):
            _require_optional_string(field, getattr(self, field))
        version = self.state_model_version
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 0
        ):
            raise ValueError("state_model_version must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class MigrationDisposition:
    disposition: str
    reason: str


@dataclass(frozen=True, slots=True)
class MigrationControl:
    """Read-only writer/activation snapshot used by migration gates."""

    phase: str
    active_writer_ids: tuple[str, ...]
    writer_compatibility_epoch: str | None
    writer_state_model_version: int | None
    v1_facts_committed: bool

    def __post_init__(self) -> None:
        _require_optional_string("phase", self.phase, required=True)
        if isinstance(self.active_writer_ids, list):
            object.__setattr__(self, "active_writer_ids", tuple(self.active_writer_ids))
        if not isinstance(self.active_writer_ids, tuple):
            raise ValueError("active_writer_ids must be a tuple")
        for writer_id in self.active_writer_ids:
            _require_optional_string("active_writer_ids", writer_id, required=True)
        if len(set(self.active_writer_ids)) != len(self.active_writer_ids):
            raise ValueError("active_writer_ids must not contain duplicates")
        _require_optional_string("writer_compatibility_epoch", self.writer_compatibility_epoch)
        version = self.writer_state_model_version
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 0
        ):
            raise ValueError("writer_state_model_version must be non-negative or None")
        if not isinstance(self.v1_facts_committed, bool):
            raise ValueError("v1_facts_committed must be a boolean")


@dataclass(frozen=True, slots=True)
class MigrationGateDecision:
    allowed: bool
    reason: str


def classify_migration_record(record: MigrationRecord) -> MigrationDisposition:
    """Classify one record without synthesizing missing history or mutating it."""

    if record.compatibility_epoch is not None:
        if record.compatibility_epoch != COMPATIBILITY_EPOCH:
            return MigrationDisposition("fail_closed", "unsupported_compatibility_epoch")
        if record.state_model_version != STATE_MODEL_VERSION:
            return MigrationDisposition("fail_closed", "unsupported_state_model_version")
    elif record.state_model_version is not None:
        return MigrationDisposition("fail_closed", "unsupported_compatibility_epoch")

    complete = _has_complete_immutable_facts(record)
    if record.v1_terminal is not None:
        if record.compatibility_epoch is None or record.state_model_version is None:
            return MigrationDisposition("fail_closed", "missing_v1_compatibility_binding")
        if not complete:
            return MigrationDisposition("conflict", "incomplete_v1_facts")
        if record.legacy_terminal is not None and record.legacy_terminal != record.v1_terminal:
            return MigrationDisposition("conflict", "legacy_v1_terminal_mismatch")
        return MigrationDisposition("trusted_v1", "v1_facts_complete")

    if record.legacy_terminal is not None and complete:
        return MigrationDisposition("reconstructable", "immutable_facts_complete")
    if record.legacy_terminal is not None:
        return MigrationDisposition("legacy_unverified", "basisless_legacy_terminal")
    return MigrationDisposition("legacy_unverified", "no_terminal_basis")


def evaluate_activation(control: MigrationControl) -> MigrationGateDecision:
    """Allow activation only for exactly one compatible v1 writer."""

    if len(control.active_writer_ids) != 1:
        return MigrationGateDecision(False, "single_writer_required")
    if control.writer_compatibility_epoch != COMPATIBILITY_EPOCH:
        return MigrationGateDecision(False, "writer_epoch_mismatch")
    if control.writer_state_model_version != STATE_MODEL_VERSION:
        return MigrationGateDecision(False, "writer_state_model_version_mismatch")
    return MigrationGateDecision(True, "single_compatible_writer")


def evaluate_rollback(control: MigrationControl) -> MigrationGateDecision:
    """Forbid legacy downgrade once any immutable v1 fact has been committed."""

    if control.v1_facts_committed:
        return MigrationGateDecision(False, "forward_only_v1_facts")
    return MigrationGateDecision(True, "preactivation_rollback")


def _has_complete_immutable_facts(record: MigrationRecord) -> bool:
    return all(
        value is not None
        for value in (
            record.ownership_digest,
            record.command_digest,
            record.basis_digest,
            record.proof_digest,
        )
    )


def _require_optional_string(field: str, value: object, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
