"""Deterministic Batch-level success policy and terminal truth table."""

from __future__ import annotations

from dataclasses import dataclass

from qarunner.domain.batch import BatchState
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError

_SCHEMA_VERSION = "qep.batch-success-policy.v1"
_EXECUTION_TERMINALS = frozenset(
    {BatchState.SUCCEEDED, BatchState.FAILED, BatchState.PARTIAL, BatchState.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class BatchSuccessPolicy:
    schema_version: str
    policy_id: str
    policy_version: int
    suite_id: str
    max_test_failed_items: int
    allow_authorized_retry_pass: bool
    allowed_test_failure_selector_digest: Digest | None
    result_mapping_schema: str
    result_mapping_version: int
    result_mapping_digest: Digest
    approval_record_digest: Digest

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            _invalid("batch_success_policy", "schema_version", "unknown")
        for field in ("policy_id", "suite_id", "result_mapping_schema"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                _invalid("batch_success_policy", field, "invalid")
        _positive("batch_success_policy", "policy_version", self.policy_version)
        _nonnegative("batch_success_policy", "max_test_failed_items", self.max_test_failed_items)
        if not isinstance(self.allow_authorized_retry_pass, bool):
            _invalid("batch_success_policy", "allow_authorized_retry_pass", "invalid")
        if self.allowed_test_failure_selector_digest is not None and not isinstance(
            self.allowed_test_failure_selector_digest, Digest
        ):
            _invalid("batch_success_policy", "allowed_test_failure_selector_digest", "invalid")
        _positive("batch_success_policy", "result_mapping_version", self.result_mapping_version)
        for field in ("result_mapping_digest", "approval_record_digest"):
            if not isinstance(getattr(self, field), Digest):
                _invalid("batch_success_policy", field, "invalid")

    @property
    def policy_digest(self) -> Digest:
        return canonical_digest(
            schema_version=self.schema_version,
            payload={
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "suite_id": self.suite_id,
                "max_test_failed_items": self.max_test_failed_items,
                "allow_authorized_retry_pass": self.allow_authorized_retry_pass,
                "allowed_test_failure_selector_digest": _value(
                    self.allowed_test_failure_selector_digest
                ),
                "result_mapping_schema": self.result_mapping_schema,
                "result_mapping_version": self.result_mapping_version,
                "result_mapping_digest": self.result_mapping_digest.value,
                "approval_record_digest": self.approval_record_digest.value,
            },
        )


@dataclass(frozen=True, slots=True)
class BatchResolutionCounts:
    original_denominator: int
    passed_count: int
    test_failed_count: int
    infra_failed_count: int
    cancelled_count: int
    unknown_lineage_count: int
    not_executed_count: int

    def __post_init__(self) -> None:
        fields = (
            "original_denominator",
            "passed_count",
            "test_failed_count",
            "infra_failed_count",
            "cancelled_count",
            "unknown_lineage_count",
            "not_executed_count",
        )
        for field in fields:
            _nonnegative("batch_resolution_counts", field, getattr(self, field))
        if self.original_denominator == 0:
            _invalid("batch_resolution_counts", "original_denominator", "empty")
        if sum(getattr(self, field) for field in fields[1:]) != self.original_denominator:
            _invalid("batch_resolution_counts", "counts", "count_mismatch")


@dataclass(frozen=True, slots=True)
class BatchOutcomeEvaluation:
    outcome: BatchState
    counts: BatchResolutionCounts
    policy_digest: Digest
    suite_id: str
    batch_cancellation_intent_digest: Digest | None

    def __post_init__(self) -> None:
        if self.outcome not in _EXECUTION_TERMINALS:
            _invalid("batch_outcome_evaluation", "outcome", "invalid")
        if not isinstance(self.counts, BatchResolutionCounts):
            _invalid("batch_outcome_evaluation", "counts", "invalid")
        if not isinstance(self.policy_digest, Digest):
            _invalid("batch_outcome_evaluation", "policy_digest", "invalid")
        if not isinstance(self.suite_id, str) or not self.suite_id.strip():
            _invalid("batch_outcome_evaluation", "suite_id", "invalid")
        if self.batch_cancellation_intent_digest is not None and not isinstance(
            self.batch_cancellation_intent_digest, Digest
        ):
            _invalid(
                "batch_outcome_evaluation",
                "batch_cancellation_intent_digest",
                "invalid",
            )

    @property
    def evaluation_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.batch-outcome-evaluation.v1",
            payload={
                "outcome": self.outcome.value,
                "original_denominator": self.counts.original_denominator,
                "passed_count": self.counts.passed_count,
                "test_failed_count": self.counts.test_failed_count,
                "infra_failed_count": self.counts.infra_failed_count,
                "cancelled_count": self.counts.cancelled_count,
                "unknown_lineage_count": self.counts.unknown_lineage_count,
                "not_executed_count": self.counts.not_executed_count,
                "policy_digest": self.policy_digest.value,
                "suite_id": self.suite_id,
                "batch_cancellation_intent_digest": _value(self.batch_cancellation_intent_digest),
            },
        )


def evaluate_batch_outcome(
    *,
    counts: BatchResolutionCounts,
    policy: BatchSuccessPolicy,
    suite_id: str,
    batch_cancellation_intent_digest: Digest | None,
) -> BatchOutcomeEvaluation:
    if not isinstance(counts, BatchResolutionCounts):
        _invalid("batch_outcome_evaluation", "counts", "invalid")
    if not isinstance(policy, BatchSuccessPolicy):
        _invalid("batch_outcome_evaluation", "policy", "invalid")
    if not isinstance(suite_id, str) or not suite_id.strip():
        _invalid("batch_outcome_evaluation", "suite_id", "invalid")
    if suite_id != policy.suite_id:
        _invalid("batch_outcome_evaluation", "suite_id", "policy_mismatch")
    if batch_cancellation_intent_digest is not None and not isinstance(
        batch_cancellation_intent_digest, Digest
    ):
        _invalid("batch_outcome_evaluation", "batch_cancellation_intent_digest", "invalid")
    if (
        policy.allow_authorized_retry_pass
        or policy.allowed_test_failure_selector_digest is not None
    ):
        _invalid("batch_outcome_evaluation", "policy", "unsupported_in_h1")
    cancel_scoped = counts.cancelled_count + counts.not_executed_count
    if cancel_scoped and batch_cancellation_intent_digest is None:
        _invalid("batch_outcome_evaluation", "cancellation_intent", "required")
    if (
        cancel_scoped == counts.original_denominator
        and counts.passed_count
        + counts.test_failed_count
        + counts.infra_failed_count
        + counts.unknown_lineage_count
        == 0
    ):
        outcome = BatchState.CANCELLED
    elif cancel_scoped + counts.unknown_lineage_count > 0:
        outcome = BatchState.PARTIAL
    elif counts.infra_failed_count > 0 or counts.test_failed_count > policy.max_test_failed_items:
        outcome = BatchState.FAILED
    else:
        outcome = BatchState.SUCCEEDED
    return BatchOutcomeEvaluation(
        outcome,
        counts,
        policy.policy_digest,
        suite_id,
        batch_cancellation_intent_digest,
    )


def _positive(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _invalid(entity, field, "invalid")


def _nonnegative(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(entity, field, "invalid")


def _value(value: Digest | None) -> str | None:
    return None if value is None else value.value


def _invalid(entity: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity, field=field, reason=reason)
