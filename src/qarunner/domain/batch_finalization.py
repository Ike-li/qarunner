"""Deterministic Batch-level success policy and terminal truth table."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from qarunner.domain.batch import BatchState
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.run_finalization import (
    RunFinalizationBasis,
    RunItemKey,
    RunItemResolution,
    RunItemResolutionSet,
    TerminalInputKind,
)

_SCHEMA_VERSION = "qep.batch-success-policy.v1"
_EXECUTION_TERMINALS = frozenset(
    {BatchState.SUCCEEDED, BatchState.FAILED, BatchState.PARTIAL, BatchState.CANCELLED}
)


class BatchItemSourceKind(StrEnum):
    RUN_RESOLUTION = "run_resolution"
    NOT_EXECUTED = "not_executed"


class BatchItemClassification(StrEnum):
    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"
    UNKNOWN_LINEAGE = "unknown_lineage"
    NOT_EXECUTED = "not_executed"


@dataclass(frozen=True, slots=True)
class BatchItemResolution:
    item_key: RunItemKey
    source_kind: BatchItemSourceKind
    source_run_id: str | None
    source_run_version: int | None
    source_run_basis_digest: Digest | None
    source_run_item_resolution_set_digest: Digest | None
    source_item_resolution_digest: Digest | None
    not_executed_fact_schema: str | None
    not_executed_fact_digest: Digest | None
    cancellation_scope_item_digest: Digest | None
    classification: BatchItemClassification

    @classmethod
    def from_run_resolution(
        cls,
        *,
        basis: RunFinalizationBasis,
        resolution_set: RunItemResolutionSet,
        resolution: RunItemResolution,
    ) -> BatchItemResolution:
        entity = "batch_item_resolution"
        if not isinstance(basis, RunFinalizationBasis):
            _invalid(entity, "basis", "invalid")
        if not isinstance(resolution_set, RunItemResolutionSet):
            _invalid(entity, "resolution_set", "invalid")
        if not isinstance(resolution, RunItemResolution):
            _invalid(entity, "resolution", "invalid")
        if (
            basis.batch_id != resolution_set.batch_id
            or basis.run_id != resolution_set.run_id
            or basis.source_run_version != resolution_set.source_run_version
            or basis.manifest_digest != resolution_set.manifest_digest
            or basis.shard_plan_digest != resolution_set.shard_plan_digest
            or basis.run_item_set_digest != resolution_set.run_item_set_digest
            or basis.item_resolution_set_digest != resolution_set.resolution_set_digest
            or basis.original_resolution_set_digest
            != resolution_set.original_resolution_set_digest
            or basis.effective_resolution_set_digest
            != resolution_set.effective_resolution_set_digest
            or basis.item_count != resolution_set.item_count
            or basis.outcome is not resolution_set.audit_outcome
            or basis.attempt_chain_digest
            != (
                None
                if basis.terminal_input_kind is TerminalInputKind.PRESTART_CANCEL
                else resolution_set.attempt_chain_digest
            )
            or basis.retry_chain_digest != resolution_set.retry_chain_digest
            or basis.adjudication_chain_digest != resolution_set.adjudication_chain_digest
        ):
            _invalid(entity, "resolution_set", "basis_mismatch")
        matching = tuple(
            entry
            for entry in resolution_set.entries
            if entry.item_key == resolution.item_key
            and entry.item_resolution_digest == resolution.item_resolution_digest
        )
        if matching != (resolution,):
            _invalid(entity, "resolution", "not_in_resolution_set")
        return cls(
            item_key=resolution.item_key,
            source_kind=BatchItemSourceKind.RUN_RESOLUTION,
            source_run_id=resolution_set.run_id,
            source_run_version=resolution_set.source_run_version,
            source_run_basis_digest=basis.basis_digest,
            source_run_item_resolution_set_digest=resolution_set.resolution_set_digest,
            source_item_resolution_digest=resolution.item_resolution_digest,
            not_executed_fact_schema=None,
            not_executed_fact_digest=None,
            cancellation_scope_item_digest=None,
            classification=BatchItemClassification(resolution.aggregation_class.value),
        )

    def __post_init__(self) -> None:
        entity = "batch_item_resolution"
        if not isinstance(self.item_key, RunItemKey):
            _invalid(entity, "item_key", "invalid")
        if not isinstance(self.source_kind, BatchItemSourceKind):
            _invalid(entity, "source_kind", "invalid")
        if not isinstance(self.classification, BatchItemClassification):
            _invalid(entity, "classification", "invalid")
        run_fields = (
            self.source_run_id,
            self.source_run_version,
            self.source_run_basis_digest,
            self.source_run_item_resolution_set_digest,
            self.source_item_resolution_digest,
        )
        not_executed_fields = (
            self.not_executed_fact_schema,
            self.not_executed_fact_digest,
            self.cancellation_scope_item_digest,
        )
        if self.source_kind is BatchItemSourceKind.RUN_RESOLUTION:
            if not isinstance(self.source_run_id, str) or not self.source_run_id.strip():
                _invalid(entity, "source_run_id", "required")
            _nonnegative(entity, "source_run_version", self.source_run_version)
            if any(not isinstance(value, Digest) for value in run_fields[2:]):
                _invalid(entity, "run_resolution_source", "invalid")
            if any(value is not None for value in not_executed_fields):
                _invalid(entity, "not_executed_source", "must_be_null")
            if self.classification is BatchItemClassification.NOT_EXECUTED:
                _invalid(entity, "classification", "source_mismatch")
        else:
            if any(value is not None for value in run_fields):
                _invalid(entity, "run_resolution_source", "must_be_null")
            if (
                not isinstance(self.not_executed_fact_schema, str)
                or not self.not_executed_fact_schema.strip()
                or not isinstance(self.not_executed_fact_digest, Digest)
                or not isinstance(self.cancellation_scope_item_digest, Digest)
            ):
                _invalid(entity, "not_executed_source", "invalid")
            if self.not_executed_fact_schema != "qep.batch-cancellation-scope-item.v1":
                _invalid(entity, "not_executed_fact_schema", "unknown")
            if self.classification is not BatchItemClassification.NOT_EXECUTED:
                _invalid(entity, "classification", "source_mismatch")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "item_key": self.item_key.canonical_payload(),
            "source_kind": self.source_kind.value,
            "source_run_id": self.source_run_id,
            "source_run_version": self.source_run_version,
            "source_run_basis_digest": _value(self.source_run_basis_digest),
            "source_run_item_resolution_set_digest": _value(
                self.source_run_item_resolution_set_digest
            ),
            "source_item_resolution_digest": _value(self.source_item_resolution_digest),
            "not_executed_fact_schema": self.not_executed_fact_schema,
            "not_executed_fact_digest": _value(self.not_executed_fact_digest),
            "cancellation_scope_item_digest": _value(self.cancellation_scope_item_digest),
            "classification": self.classification.value,
        }

    @property
    def batch_item_resolution_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.batch-item-resolution-set.v1",
            payload={"projection_kind": "item", **self.canonical_payload()},
        )


@dataclass(frozen=True, slots=True)
class BatchItemResolutionSet:
    batch_id: str
    source_batch_version: int
    manifest_id: str
    manifest_digest: Digest
    shard_plan_id: str
    shard_plan_version: int
    shard_plan_digest: Digest
    canonical_run_set_digest: Digest
    expected_item_keys: tuple[RunItemKey, ...]
    entries: tuple[BatchItemResolution, ...]

    def __post_init__(self) -> None:
        entity = "batch_item_resolution_set"
        for field in ("batch_id", "manifest_id", "shard_plan_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                _invalid(entity, field, "invalid")
        _nonnegative(entity, "source_batch_version", self.source_batch_version)
        _nonnegative(entity, "shard_plan_version", self.shard_plan_version)
        for field in ("manifest_digest", "shard_plan_digest", "canonical_run_set_digest"):
            if not isinstance(getattr(self, field), Digest):
                _invalid(entity, field, "invalid")
        if not isinstance(self.entries, tuple) or not self.entries:
            _invalid(entity, "entries", "invalid")
        if any(not isinstance(entry, BatchItemResolution) for entry in self.entries):
            _invalid(entity, "entries", "invalid")
        if (
            not isinstance(self.expected_item_keys, tuple)
            or not self.expected_item_keys
            or any(not isinstance(key, RunItemKey) for key in self.expected_item_keys)
        ):
            _invalid(entity, "expected_item_keys", "invalid")
        if len(set(self.expected_item_keys)) != len(self.expected_item_keys):
            _invalid(entity, "expected_item_keys", "duplicate_key")
        if self.expected_item_keys != tuple(sorted(self.expected_item_keys)):
            _invalid(entity, "expected_item_keys", "not_ordered")
        if any(key.manifest_id != self.manifest_id for key in self.expected_item_keys):
            _invalid(entity, "expected_item_keys", "manifest_mismatch")
        keys = tuple(entry.item_key for entry in self.entries)
        if any(key.manifest_id != self.manifest_id for key in keys):
            _invalid(entity, "entries", "manifest_mismatch")
        if len(set(keys)) != len(keys):
            _invalid(entity, "entries", "duplicate_or_overlapping_key")
        if keys != tuple(sorted(keys)):
            _invalid(entity, "entries", "not_ordered")
        if keys != self.expected_item_keys:
            _invalid(entity, "entries", "manifest_coverage_mismatch")

    @classmethod
    def build(
        cls,
        *,
        expected_item_keys: tuple[RunItemKey, ...],
        entries: tuple[BatchItemResolution, ...],
        **envelope: object,
    ) -> BatchItemResolutionSet:
        entity = "batch_item_resolution_set"
        if (
            not isinstance(expected_item_keys, tuple)
            or not expected_item_keys
            or any(not isinstance(key, RunItemKey) for key in expected_item_keys)
        ):
            _invalid(entity, "expected_item_keys", "invalid")
        if len(set(expected_item_keys)) != len(expected_item_keys):
            _invalid(entity, "expected_item_keys", "duplicate_key")
        if not isinstance(entries, tuple) or any(
            not isinstance(entry, BatchItemResolution) for entry in entries
        ):
            _invalid(entity, "entries", "invalid")
        entry_keys = tuple(entry.item_key for entry in entries)
        if len(set(entry_keys)) != len(entry_keys):
            _invalid(entity, "entries", "duplicate_or_overlapping_key")
        if set(entry_keys) != set(expected_item_keys):
            _invalid(entity, "entries", "manifest_coverage_mismatch")
        return cls(
            expected_item_keys=tuple(sorted(expected_item_keys)),
            entries=tuple(sorted(entries, key=lambda entry: entry.item_key)),
            **envelope,
        )

    @property
    def schema_version(self) -> str:
        return "qep.batch-item-resolution-set.v1"

    @property
    def counts(self) -> BatchResolutionCounts:
        values = {classification: 0 for classification in BatchItemClassification}
        for entry in self.entries:
            values[entry.classification] += 1
        return BatchResolutionCounts(
            original_denominator=len(self.entries),
            passed_count=values[BatchItemClassification.PASSED],
            test_failed_count=values[BatchItemClassification.TEST_FAILED],
            infra_failed_count=values[BatchItemClassification.INFRA_FAILED],
            cancelled_count=values[BatchItemClassification.CANCELLED],
            unknown_lineage_count=values[BatchItemClassification.UNKNOWN_LINEAGE],
            not_executed_count=values[BatchItemClassification.NOT_EXECUTED],
        )

    @property
    def item_count(self) -> int:
        return self.counts.original_denominator

    @property
    def passed_count(self) -> int:
        return self.counts.passed_count

    @property
    def test_failed_count(self) -> int:
        return self.counts.test_failed_count

    @property
    def infra_failed_count(self) -> int:
        return self.counts.infra_failed_count

    @property
    def cancelled_count(self) -> int:
        return self.counts.cancelled_count

    @property
    def unknown_lineage_count(self) -> int:
        return self.counts.unknown_lineage_count

    @property
    def not_executed_count(self) -> int:
        return self.counts.not_executed_count

    def canonical_payload(self) -> dict[str, object]:
        counts = self.counts
        return {
            "batch_id": self.batch_id,
            "source_batch_version": self.source_batch_version,
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest.value,
            "shard_plan_id": self.shard_plan_id,
            "shard_plan_version": self.shard_plan_version,
            "shard_plan_digest": self.shard_plan_digest.value,
            "canonical_run_set_digest": self.canonical_run_set_digest.value,
            "entries": [
                {
                    **entry.canonical_payload(),
                    "batch_item_resolution_digest": entry.batch_item_resolution_digest.value,
                }
                for entry in self.entries
            ],
            "item_count": counts.original_denominator,
            "passed_count": counts.passed_count,
            "test_failed_count": counts.test_failed_count,
            "infra_failed_count": counts.infra_failed_count,
            "cancelled_count": counts.cancelled_count,
            "unknown_lineage_count": counts.unknown_lineage_count,
            "not_executed_count": counts.not_executed_count,
        }

    @property
    def resolution_set_digest(self) -> Digest:
        return canonical_digest(
            schema_version=self.schema_version, payload=self.canonical_payload()
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
