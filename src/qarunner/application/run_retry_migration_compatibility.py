"""Pure M0 compatibility decisions for immutable Run retry chains."""

from dataclasses import dataclass
from enum import StrEnum

COMPATIBILITY_EPOCH = "M0-STATE-V1"
STATE_MODEL_VERSION = 1


class RetryMigrationKind(StrEnum):
    POLICY = "policy"
    CONFIRM_STOPPED = "confirm_stopped"
    DUPLICATE_RISK = "duplicate_risk"


@dataclass(frozen=True, slots=True)
class RunRetryMigrationRecord:
    record_id: str
    compatibility_epoch: str | None
    state_model_version: int | None
    retry_kind: RetryMigrationKind | None
    intent_digest: str | None
    receipt_digest: str | None
    authority_digest: str | None
    queue_identity_scope_digest: str | None
    queued_run_version: int | None
    queue_writer_digest: str | None
    queue_write_epoch: int | None
    reservation_digest: str | None
    acceptance_consumption_digest: str | None
    start_commit_digest: str | None
    attempt_provenance_digest: str | None

    def __post_init__(self) -> None:
        if isinstance(self.retry_kind, str):
            try:
                object.__setattr__(self, "retry_kind", RetryMigrationKind(self.retry_kind))
            except ValueError:
                raise ValueError("retry_kind must be a known RetryMigrationKind or None") from None
        _string("record_id", self.record_id, required=True)
        _string("compatibility_epoch", self.compatibility_epoch)
        for field in (
            "intent_digest",
            "receipt_digest",
            "authority_digest",
            "queue_identity_scope_digest",
            "queue_writer_digest",
            "reservation_digest",
            "acceptance_consumption_digest",
            "start_commit_digest",
            "attempt_provenance_digest",
        ):
            _string(field, getattr(self, field))
        _version("state_model_version", self.state_model_version)
        _version("queued_run_version", self.queued_run_version)
        _positive_version("queue_write_epoch", self.queue_write_epoch)
        if self.retry_kind is not None and not isinstance(self.retry_kind, RetryMigrationKind):
            raise ValueError("retry_kind must be a known RetryMigrationKind or None")


@dataclass(frozen=True, slots=True)
class RunRetryMigrationDisposition:
    disposition: str
    reason: str


@dataclass(frozen=True, slots=True)
class RunRetryMigrationControl:
    active_writer_ids: tuple[str, ...]
    writer_compatibility_epoch: str | None
    writer_state_model_version: int | None
    v1_retry_facts_committed: bool

    def __post_init__(self) -> None:
        if isinstance(self.active_writer_ids, list):
            object.__setattr__(self, "active_writer_ids", tuple(self.active_writer_ids))
        if not isinstance(self.active_writer_ids, tuple):
            raise ValueError("active_writer_ids must be a tuple")
        for value in self.active_writer_ids:
            _string("active_writer_ids", value, required=True)
        if len(set(self.active_writer_ids)) != len(self.active_writer_ids):
            raise ValueError("active_writer_ids must not contain duplicates")
        _string("writer_compatibility_epoch", self.writer_compatibility_epoch)
        _version("writer_state_model_version", self.writer_state_model_version)
        if not isinstance(self.v1_retry_facts_committed, bool):
            raise ValueError("v1_retry_facts_committed must be a boolean")


@dataclass(frozen=True, slots=True)
class RunRetryMigrationGate:
    allowed: bool
    reason: str


def classify_run_retry_record(record: RunRetryMigrationRecord) -> RunRetryMigrationDisposition:
    if record.compatibility_epoch is not None:
        if record.compatibility_epoch != COMPATIBILITY_EPOCH:
            return RunRetryMigrationDisposition("fail_closed", "unsupported_compatibility_epoch")
        if record.state_model_version != STATE_MODEL_VERSION:
            return RunRetryMigrationDisposition("fail_closed", "unsupported_state_model_version")
    elif record.state_model_version is not None:
        return RunRetryMigrationDisposition("fail_closed", "missing_compatibility_epoch")

    if record.compatibility_epoch is None:
        return RunRetryMigrationDisposition(
            "legacy_unverified",
            "basisless_legacy_retry" if record.intent_digest else "no_retry_basis",
        )
    if (
        not all(
            (
                record.intent_digest,
                record.receipt_digest,
                record.authority_digest,
                record.queue_identity_scope_digest,
                record.queue_writer_digest,
            )
        )
        or record.queued_run_version is None
        or record.queue_write_epoch is None
    ):
        return RunRetryMigrationDisposition("conflict", "incomplete_retry_chain")
    if record.retry_kind is None:
        return RunRetryMigrationDisposition("conflict", "missing_retry_kind")
    reservation = record.reservation_digest is not None
    consumption = record.acceptance_consumption_digest is not None
    expected = {
        RetryMigrationKind.POLICY: (True, False),
        RetryMigrationKind.CONFIRM_STOPPED: (False, False),
        RetryMigrationKind.DUPLICATE_RISK: (False, True),
    }[record.retry_kind]
    if (reservation, consumption) != expected:
        return RunRetryMigrationDisposition("conflict", "retry_participant_kind_mismatch")
    if record.attempt_provenance_digest is not None and record.start_commit_digest is None:
        return RunRetryMigrationDisposition("conflict", "attempt_without_start_commit")
    if record.start_commit_digest is not None and record.attempt_provenance_digest is None:
        return RunRetryMigrationDisposition("conflict", "missing_attempt_provenance")
    return RunRetryMigrationDisposition("trusted_v1", "immutable_retry_chain_complete")


def evaluate_run_retry_activation(control: RunRetryMigrationControl) -> RunRetryMigrationGate:
    if len(control.active_writer_ids) != 1:
        return RunRetryMigrationGate(False, "single_writer_required")
    if control.writer_compatibility_epoch != COMPATIBILITY_EPOCH:
        return RunRetryMigrationGate(False, "writer_epoch_mismatch")
    if control.writer_state_model_version != STATE_MODEL_VERSION:
        return RunRetryMigrationGate(False, "writer_state_model_version_mismatch")
    return RunRetryMigrationGate(True, "single_compatible_writer")


def evaluate_run_retry_rollback(control: RunRetryMigrationControl) -> RunRetryMigrationGate:
    if control.v1_retry_facts_committed:
        return RunRetryMigrationGate(False, "forward_only_v1_retry_facts")
    return RunRetryMigrationGate(True, "preactivation_rollback")


def _string(field: str, value: object, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _version(field: str, value: object) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{field} must be a non-negative integer or None")


def _positive_version(field: str, value: object) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        raise ValueError(f"{field} must be a positive integer or None")
