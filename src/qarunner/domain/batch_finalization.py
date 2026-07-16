"""Deterministic Batch-level success policy and terminal truth table."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from qarunner.domain.batch import BatchState
from qarunner.domain.cancellation import (
    BatchCancellationResolutionKind,
    BatchCancellationScopeItem,
    canonicalize_batch_cancellation_scope_items,
)
from qarunner.domain.digest import (
    Digest,
    canonical_digest,
    canonical_materialized_run_set_digest,
)
from qarunner.domain.errors import DomainValidationError
from qarunner.domain.run_finalization import (
    RunFinalizationBasis,
    RunItemKey,
    RunItemResolution,
    RunItemResolutionSet,
    RunOutcome,
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
class BatchTerminalRunRef:
    run_id: str
    source_run_version: int
    run_basis_digest: Digest
    run_outcome: RunOutcome
    run_item_set_digest: Digest
    original_resolution_set_digest: Digest
    effective_resolution_set_digest: Digest
    item_resolution_set_digest: Digest

    def __post_init__(self) -> None:
        entity = "batch_terminal_run_ref"
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            _invalid(entity, "run_id", "invalid")
        _nonnegative(entity, "source_run_version", self.source_run_version)
        if not isinstance(self.run_outcome, RunOutcome):
            _invalid(entity, "run_outcome", "invalid")
        for field in (
            "run_basis_digest",
            "run_item_set_digest",
            "original_resolution_set_digest",
            "effective_resolution_set_digest",
            "item_resolution_set_digest",
        ):
            if not isinstance(getattr(self, field), Digest):
                _invalid(entity, field, "invalid")

    @classmethod
    def from_basis(cls, *, basis: RunFinalizationBasis) -> BatchTerminalRunRef:
        if not isinstance(basis, RunFinalizationBasis):
            _invalid("batch_terminal_run_ref", "basis", "invalid")
        return cls(
            run_id=basis.run_id,
            source_run_version=basis.source_run_version,
            run_basis_digest=basis.basis_digest,
            run_outcome=basis.outcome,
            run_item_set_digest=basis.run_item_set_digest,
            original_resolution_set_digest=basis.original_resolution_set_digest,
            effective_resolution_set_digest=basis.effective_resolution_set_digest,
            item_resolution_set_digest=basis.item_resolution_set_digest,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "source_run_version": self.source_run_version,
            "run_basis_digest": self.run_basis_digest.value,
            "run_outcome": self.run_outcome.value,
            "run_item_set_digest": self.run_item_set_digest.value,
            "original_resolution_set_digest": self.original_resolution_set_digest.value,
            "effective_resolution_set_digest": self.effective_resolution_set_digest.value,
            "item_resolution_set_digest": self.item_resolution_set_digest.value,
        }


@dataclass(frozen=True, slots=True, order=True)
class BatchNonRunResolutionRef:
    item_key: RunItemKey
    not_executed_fact_schema: str
    not_executed_fact_digest: Digest

    def __post_init__(self) -> None:
        entity = "batch_non_run_resolution_ref"
        if not isinstance(self.item_key, RunItemKey):
            _invalid(entity, "item_key", "invalid")
        if self.not_executed_fact_schema != "qep.batch-cancellation-scope-item.v1":
            _invalid(entity, "not_executed_fact_schema", "unknown")
        if not isinstance(self.not_executed_fact_digest, Digest):
            _invalid(entity, "not_executed_fact_digest", "invalid")

    @classmethod
    def from_scope_item(cls, *, fact: BatchCancellationScopeItem) -> BatchNonRunResolutionRef:
        if not isinstance(fact, BatchCancellationScopeItem):
            _invalid("batch_non_run_resolution_ref", "fact", "invalid")
        if fact.resolution_kind is not BatchCancellationResolutionKind.NOT_EXECUTED:
            _invalid("batch_non_run_resolution_ref", "fact", "not_not_executed")
        return cls(
            item_key=fact.manifest_item_key,
            not_executed_fact_schema="qep.batch-cancellation-scope-item.v1",
            not_executed_fact_digest=fact.scope_item_digest,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "item_key": self.item_key.canonical_payload(),
            "not_executed_fact_schema": self.not_executed_fact_schema,
            "not_executed_fact_digest": self.not_executed_fact_digest.value,
        }


@dataclass(frozen=True, slots=True, order=True)
class BatchUnknownFactRef:
    item_key: RunItemKey
    source_item_resolution_digest: Digest
    unknown_lineage_digest: Digest
    adjudication_digest: Digest

    def __post_init__(self) -> None:
        entity = "batch_unknown_fact_ref"
        if not isinstance(self.item_key, RunItemKey):
            _invalid(entity, "item_key", "invalid")
        for field in (
            "source_item_resolution_digest",
            "unknown_lineage_digest",
            "adjudication_digest",
        ):
            if not isinstance(getattr(self, field), Digest):
                _invalid(entity, field, "invalid")

    @classmethod
    def from_resolution(cls, *, resolution: RunItemResolution) -> BatchUnknownFactRef:
        if not isinstance(resolution, RunItemResolution):
            _invalid("batch_unknown_fact_ref", "resolution", "invalid")
        if (
            resolution.unknown_lineage_digest is None
            or resolution.effective.adjudication_digest is None
        ):
            _invalid("batch_unknown_fact_ref", "resolution", "not_unknown_lineage")
        return cls(
            item_key=resolution.item_key,
            source_item_resolution_digest=resolution.item_resolution_digest,
            unknown_lineage_digest=resolution.unknown_lineage_digest,
            adjudication_digest=resolution.effective.adjudication_digest,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "item_key": self.item_key.canonical_payload(),
            "source_item_resolution_digest": self.source_item_resolution_digest.value,
            "unknown_lineage_digest": self.unknown_lineage_digest.value,
            "adjudication_digest": self.adjudication_digest.value,
        }


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
    def from_not_executed(cls, *, fact: BatchCancellationScopeItem) -> BatchItemResolution:
        entity = "batch_item_resolution"
        if not isinstance(fact, BatchCancellationScopeItem):
            _invalid(entity, "fact", "invalid")
        if fact.resolution_kind is not BatchCancellationResolutionKind.NOT_EXECUTED:
            _invalid(entity, "fact", "not_not_executed")
        return cls(
            item_key=fact.manifest_item_key,
            source_kind=BatchItemSourceKind.NOT_EXECUTED,
            source_run_id=None,
            source_run_version=None,
            source_run_basis_digest=None,
            source_run_item_resolution_set_digest=None,
            source_item_resolution_digest=None,
            not_executed_fact_schema="qep.batch-cancellation-scope-item.v1",
            not_executed_fact_digest=fact.scope_item_digest,
            cancellation_scope_item_digest=fact.scope_item_digest,
            classification=BatchItemClassification.NOT_EXECUTED,
        )

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
            if self.not_executed_fact_digest != self.cancellation_scope_item_digest:
                _invalid(entity, "not_executed_source", "digest_mismatch")
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
        run_sources: dict[str, tuple[int, Digest, Digest]] = {}
        basis_owners: dict[Digest, str] = {}
        resolution_set_owners: dict[Digest, str] = {}
        item_digests: set[Digest] = set()
        for entry in self.entries:
            if entry.source_kind is not BatchItemSourceKind.RUN_RESOLUTION:
                continue
            run_id = cast(str, entry.source_run_id)
            source = (
                cast(int, entry.source_run_version),
                cast(Digest, entry.source_run_basis_digest),
                cast(Digest, entry.source_run_item_resolution_set_digest),
            )
            prior = run_sources.setdefault(run_id, source)
            if prior != source:
                _invalid(entity, "entries", "run_source_incoherent")
            basis_owner = basis_owners.setdefault(source[1], run_id)
            resolution_set_owner = resolution_set_owners.setdefault(source[2], run_id)
            item_digest = cast(Digest, entry.source_item_resolution_digest)
            if (
                basis_owner != run_id
                or resolution_set_owner != run_id
                or item_digest in item_digests
            ):
                _invalid(entity, "entries", "run_source_overlap")
            item_digests.add(item_digest)

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
class BatchFinalizationBasis:
    resolution_set: BatchItemResolutionSet
    run_resolution_sets: tuple[RunItemResolutionSet, ...]
    run_bases: tuple[RunFinalizationBasis, ...]
    cancellation_scope_items: tuple[BatchCancellationScopeItem, ...]
    terminal_run_refs: tuple[BatchTerminalRunRef, ...]
    non_run_refs: tuple[BatchNonRunResolutionRef, ...]
    unknown_fact_refs: tuple[BatchUnknownFactRef, ...]
    policy: BatchSuccessPolicy
    evaluation: BatchOutcomeEvaluation

    def __post_init__(self) -> None:
        entity = "batch_finalization_basis"
        if not isinstance(self.resolution_set, BatchItemResolutionSet):
            _invalid(entity, "resolution_set", "invalid")
        if not isinstance(self.policy, BatchSuccessPolicy):
            _invalid(entity, "policy", "invalid")
        if not isinstance(self.evaluation, BatchOutcomeEvaluation):
            _invalid(entity, "evaluation", "invalid")
        if not isinstance(self.run_resolution_sets, tuple) or any(
            not isinstance(value, RunItemResolutionSet) for value in self.run_resolution_sets
        ):
            _invalid(entity, "run_resolution_sets", "invalid")
        if not isinstance(self.run_bases, tuple) or any(
            not isinstance(value, RunFinalizationBasis) for value in self.run_bases
        ):
            _invalid(entity, "run_bases", "invalid")
        if not isinstance(self.cancellation_scope_items, tuple) or any(
            not isinstance(value, BatchCancellationScopeItem)
            for value in self.cancellation_scope_items
        ):
            _invalid(entity, "cancellation_scope_items", "invalid")
        run_sets_by_id = self._unique_by_run_id(
            entity, "run_resolution_sets", self.run_resolution_sets
        )
        bases_by_id = self._unique_by_run_id(entity, "run_bases", self.run_bases)
        if set(run_sets_by_id) != set(bases_by_id):
            _invalid(entity, "run_bases", "run_set_mismatch")
        derived_run_entries: dict[RunItemKey, BatchItemResolution] = {}
        expected_unknown_refs: list[BatchUnknownFactRef] = []
        for run_id, run_set in run_sets_by_id.items():
            basis = cast(RunFinalizationBasis, bases_by_id[run_id])
            if (
                run_set.batch_id != self.resolution_set.batch_id
                or run_set.manifest_digest != self.resolution_set.manifest_digest
                or run_set.shard_plan_digest != self.resolution_set.shard_plan_digest
            ):
                _invalid(entity, "run_resolution_sets", "envelope_mismatch")
            for item in run_set.entries:
                derived = BatchItemResolution.from_run_resolution(
                    basis=basis,
                    resolution_set=run_set,
                    resolution=item,
                )
                if derived.item_key in derived_run_entries:
                    _invalid(entity, "run_resolution_sets", "item_overlap")
                derived_run_entries[derived.item_key] = derived
                if item.unknown_lineage_digest is not None:
                    expected_unknown_refs.append(
                        BatchUnknownFactRef.from_resolution(resolution=item)
                    )
        actual_run_entries = {
            entry.item_key: entry
            for entry in self.resolution_set.entries
            if entry.source_kind is BatchItemSourceKind.RUN_RESOLUTION
        }
        if actual_run_entries != derived_run_entries:
            _invalid(entity, "resolution_set", "run_sources_mismatch")
        expected_evaluation = evaluate_batch_outcome(
            counts=self.resolution_set.counts,
            policy=self.policy,
            suite_id=self.policy.suite_id,
            batch_cancellation_intent_digest=(self.evaluation.batch_cancellation_intent_digest),
        )
        if self.evaluation != expected_evaluation:
            _invalid(entity, "evaluation", "result_mismatch")
        self._require_ordered_refs(
            entity,
            "terminal_run_refs",
            self.terminal_run_refs,
            BatchTerminalRunRef,
            lambda ref: ref.run_id,
        )
        expected_terminal_refs = tuple(
            sorted(
                (BatchTerminalRunRef.from_basis(basis=basis) for basis in self.run_bases),
                key=lambda ref: ref.run_id,
            )
        )
        if self.terminal_run_refs != expected_terminal_refs:
            _invalid(entity, "terminal_run_refs", "basis_mismatch")
        self._require_ordered_refs(
            entity,
            "non_run_refs",
            self.non_run_refs,
            BatchNonRunResolutionRef,
            lambda ref: ref.item_key,
        )
        if self.canonical_run_set_digest != canonical_materialized_run_set_digest(
            batch_id=self.batch_id,
            run_ids=tuple(run_sets_by_id),
        ):
            _invalid(entity, "canonical_run_set_digest", "actual_run_set_mismatch")
        non_run_by_key = {ref.item_key: ref for ref in self.non_run_refs}
        expected_non_run = {
            entry.item_key: entry.not_executed_fact_digest
            for entry in self.resolution_set.entries
            if entry.source_kind is BatchItemSourceKind.NOT_EXECUTED
        }
        if {
            key: ref.not_executed_fact_digest for key, ref in non_run_by_key.items()
        } != expected_non_run:
            _invalid(entity, "non_run_refs", "count_mismatch")
        if self.batch_cancellation_intent_digest is None:
            if self.cancellation_scope_items:
                _invalid(entity, "cancellation_scope_items", "unexpected")
        else:
            canonical_scope_items = canonicalize_batch_cancellation_scope_items(
                batch_id=self.batch_id,
                batch_cancellation_intent_digest=self.batch_cancellation_intent_digest,
                manifest_id=self.manifest_id,
                manifest_digest=self.manifest_digest,
                shard_plan_version=self.shard_plan_version,
                shard_plan_digest=self.shard_plan_digest,
                expected_item_keys=self.resolution_set.expected_item_keys,
                items=self.cancellation_scope_items,
            )
            expected_non_run_refs = tuple(
                BatchNonRunResolutionRef.from_scope_item(fact=fact)
                for fact in canonical_scope_items
                if fact.resolution_kind is BatchCancellationResolutionKind.NOT_EXECUTED
            )
            if self.non_run_refs != expected_non_run_refs:
                _invalid(entity, "non_run_refs", "scope_fact_mismatch")
        if len(self.unknown_fact_refs) != self.counts.unknown_lineage_count:
            _invalid(entity, "unknown_fact_refs", "count_mismatch")
        self._require_ordered_refs(
            entity,
            "unknown_fact_refs",
            self.unknown_fact_refs,
            BatchUnknownFactRef,
            lambda ref: ref.item_key,
        )
        if self.unknown_fact_refs != tuple(
            sorted(expected_unknown_refs, key=lambda ref: ref.item_key)
        ):
            _invalid(entity, "unknown_fact_refs", "resolution_mismatch")

    @property
    def batch_id(self) -> str:
        return self.resolution_set.batch_id

    @property
    def source_batch_version(self) -> int:
        return self.resolution_set.source_batch_version

    @property
    def manifest_id(self) -> str:
        return self.resolution_set.manifest_id

    @property
    def manifest_digest(self) -> Digest:
        return self.resolution_set.manifest_digest

    @property
    def item_count(self) -> int:
        return self.resolution_set.item_count

    @property
    def shard_plan_id(self) -> str:
        return self.resolution_set.shard_plan_id

    @property
    def shard_plan_version(self) -> int:
        return self.resolution_set.shard_plan_version

    @property
    def shard_plan_digest(self) -> Digest:
        return self.resolution_set.shard_plan_digest

    @property
    def canonical_run_set_digest(self) -> Digest:
        return self.resolution_set.canonical_run_set_digest

    @property
    def success_policy_id(self) -> str:
        return self.policy.policy_id

    @property
    def success_policy_version(self) -> int:
        return self.policy.policy_version

    @property
    def success_policy_digest(self) -> Digest:
        return self.policy.policy_digest

    @property
    def batch_item_resolution_schema(self) -> str:
        return self.resolution_set.schema_version

    @property
    def batch_item_resolution_set_digest(self) -> Digest:
        return self.resolution_set.resolution_set_digest

    @property
    def counts(self) -> BatchResolutionCounts:
        return self.resolution_set.counts

    @property
    def batch_cancellation_intent_digest(self) -> Digest | None:
        return self.evaluation.batch_cancellation_intent_digest

    @property
    def batch_outcome(self) -> BatchState:
        return self.evaluation.outcome

    @property
    def completeness_proof_digest(self) -> Digest:
        return self._build_completeness_proof(
            resolution_set=self.resolution_set,
            terminal_run_refs=self.terminal_run_refs,
            unknown_fact_refs=self.unknown_fact_refs,
        )

    @staticmethod
    def _require_ordered_refs(
        entity: str,
        field: str,
        refs: object,
        expected_type: type,
        key: object,
    ) -> None:
        if not isinstance(refs, tuple) or any(not isinstance(ref, expected_type) for ref in refs):
            _invalid(entity, field, "invalid")
        keys = tuple(key(ref) for ref in refs)  # type: ignore[operator]
        if len(set(keys)) != len(keys):
            _invalid(entity, field, "duplicate")
        if keys != tuple(sorted(keys)):
            _invalid(entity, field, "not_ordered")

    @classmethod
    def build(
        cls,
        *,
        resolution_set: BatchItemResolutionSet,
        run_resolution_sets: tuple[RunItemResolutionSet, ...],
        run_bases: tuple[RunFinalizationBasis, ...],
        cancellation_scope_items: tuple[BatchCancellationScopeItem, ...],
        policy: BatchSuccessPolicy,
        evaluation: BatchOutcomeEvaluation,
    ) -> BatchFinalizationBasis:
        entity = "batch_finalization_basis"
        if not isinstance(resolution_set, BatchItemResolutionSet):
            _invalid(entity, "resolution_set", "invalid")
        if not isinstance(policy, BatchSuccessPolicy):
            _invalid(entity, "policy", "invalid")
        if not isinstance(evaluation, BatchOutcomeEvaluation):
            _invalid(entity, "evaluation", "invalid")
        if evaluation.counts != resolution_set.counts:
            _invalid(entity, "evaluation", "counts_mismatch")
        if evaluation.policy_digest != policy.policy_digest:
            _invalid(entity, "evaluation", "policy_mismatch")
        expected_evaluation = evaluate_batch_outcome(
            counts=resolution_set.counts,
            policy=policy,
            suite_id=policy.suite_id,
            batch_cancellation_intent_digest=evaluation.batch_cancellation_intent_digest,
        )
        if evaluation != expected_evaluation:
            _invalid(entity, "evaluation", "result_mismatch")
        if not isinstance(run_resolution_sets, tuple) or any(
            not isinstance(value, RunItemResolutionSet) for value in run_resolution_sets
        ):
            _invalid(entity, "run_resolution_sets", "invalid")
        if not isinstance(run_bases, tuple) or any(
            not isinstance(value, RunFinalizationBasis) for value in run_bases
        ):
            _invalid(entity, "run_bases", "invalid")
        run_sets_by_id = cls._unique_by_run_id(entity, "run_resolution_sets", run_resolution_sets)
        bases_by_id = cls._unique_by_run_id(entity, "run_bases", run_bases)
        expected_run_set_digest = canonical_materialized_run_set_digest(
            batch_id=resolution_set.batch_id,
            run_ids=tuple(run_sets_by_id),
        )
        if resolution_set.canonical_run_set_digest != expected_run_set_digest:
            _invalid(entity, "canonical_run_set_digest", "actual_run_set_mismatch")
        if set(run_sets_by_id) != set(bases_by_id):
            _invalid(entity, "run_bases", "run_set_mismatch")
        batch_entries = {entry.item_key: entry for entry in resolution_set.entries}
        derived_entries: dict[RunItemKey, BatchItemResolution] = {}
        unknown_refs: list[BatchUnknownFactRef] = []
        terminal_refs: list[BatchTerminalRunRef] = []
        for run_id, run_set in run_sets_by_id.items():
            basis = bases_by_id[run_id]
            if (
                run_set.batch_id != resolution_set.batch_id
                or run_set.manifest_digest != resolution_set.manifest_digest
                or run_set.shard_plan_digest != resolution_set.shard_plan_digest
            ):
                _invalid(entity, "run_resolution_sets", "envelope_mismatch")
            terminal_refs.append(BatchTerminalRunRef.from_basis(basis=basis))
            for item in run_set.entries:
                entry = BatchItemResolution.from_run_resolution(
                    basis=basis, resolution_set=run_set, resolution=item
                )
                if entry.item_key in derived_entries:
                    _invalid(entity, "run_resolution_sets", "item_overlap")
                derived_entries[entry.item_key] = entry
                if entry.classification is BatchItemClassification.UNKNOWN_LINEAGE:
                    unknown_refs.append(BatchUnknownFactRef.from_resolution(resolution=item))
        run_entries = {
            key: entry
            for key, entry in batch_entries.items()
            if entry.source_kind is BatchItemSourceKind.RUN_RESOLUTION
        }
        if derived_entries != run_entries:
            _invalid(entity, "run_resolution_sets", "item_resolution_mismatch")
        non_run_refs: list[BatchNonRunResolutionRef] = []
        if evaluation.batch_cancellation_intent_digest is None:
            if cancellation_scope_items:
                _invalid(entity, "cancellation_scope_items", "unexpected")
        else:
            canonical_scope = canonicalize_batch_cancellation_scope_items(
                batch_id=resolution_set.batch_id,
                batch_cancellation_intent_digest=evaluation.batch_cancellation_intent_digest,
                manifest_id=resolution_set.manifest_id,
                manifest_digest=resolution_set.manifest_digest,
                shard_plan_version=resolution_set.shard_plan_version,
                shard_plan_digest=resolution_set.shard_plan_digest,
                expected_item_keys=resolution_set.expected_item_keys,
                items=cancellation_scope_items,
            )
            for fact in canonical_scope:
                entry = batch_entries[fact.manifest_item_key]
                if fact.resolution_kind is BatchCancellationResolutionKind.NOT_EXECUTED:
                    expected = BatchItemResolution.from_not_executed(fact=fact)
                    if entry != expected:
                        _invalid(entity, "cancellation_scope_items", "not_executed_mismatch")
                    non_run_refs.append(BatchNonRunResolutionRef.from_scope_item(fact=fact))
                elif (
                    entry.source_kind is not BatchItemSourceKind.RUN_RESOLUTION
                    or entry.source_run_id != fact.run_id
                    or entry.source_run_version != fact.source_run_version
                ):
                    _invalid(entity, "cancellation_scope_items", "run_fanout_mismatch")
        not_executed_entries = tuple(
            entry
            for entry in resolution_set.entries
            if entry.source_kind is BatchItemSourceKind.NOT_EXECUTED
        )
        if len(non_run_refs) != len(not_executed_entries):
            _invalid(entity, "cancellation_scope_items", "not_executed_coverage_mismatch")
        terminal_refs_tuple = tuple(sorted(terminal_refs, key=lambda ref: ref.run_id))
        non_run_refs_tuple = tuple(sorted(non_run_refs, key=lambda ref: ref.item_key))
        unknown_refs_tuple = tuple(sorted(unknown_refs, key=lambda ref: ref.item_key))
        return cls(
            resolution_set=resolution_set,
            run_resolution_sets=run_resolution_sets,
            run_bases=run_bases,
            cancellation_scope_items=cancellation_scope_items,
            terminal_run_refs=terminal_refs_tuple,
            non_run_refs=non_run_refs_tuple,
            unknown_fact_refs=unknown_refs_tuple,
            policy=policy,
            evaluation=evaluation,
        )

    @staticmethod
    def _unique_by_run_id(entity: str, field: str, values: tuple) -> dict[str, object]:
        result: dict[str, object] = {}
        for value in values:
            if value.run_id in result:
                _invalid(entity, field, "duplicate_run")
            result[value.run_id] = value
        return result

    @staticmethod
    def _build_completeness_proof(
        *,
        resolution_set: BatchItemResolutionSet,
        terminal_run_refs: tuple[BatchTerminalRunRef, ...],
        unknown_fact_refs: tuple[BatchUnknownFactRef, ...],
    ) -> Digest:
        unknown_by_key = {ref.item_key: ref for ref in unknown_fact_refs}
        item_bindings = []
        for entry in resolution_set.entries:
            unknown = unknown_by_key.get(entry.item_key)
            item_bindings.append(
                {
                    **entry.canonical_payload(),
                    "batch_item_resolution_digest": entry.batch_item_resolution_digest.value,
                    "unknown_lineage_digest": (
                        None if unknown is None else unknown.unknown_lineage_digest.value
                    ),
                    "adjudication_digest": (
                        None if unknown is None else unknown.adjudication_digest.value
                    ),
                }
            )
        return canonical_digest(
            schema_version="qep.batch-completeness-proof.v1",
            payload={
                "batch_id": resolution_set.batch_id,
                "source_batch_version": resolution_set.source_batch_version,
                "manifest_id": resolution_set.manifest_id,
                "manifest_digest": resolution_set.manifest_digest.value,
                "item_count": resolution_set.item_count,
                "shard_plan_id": resolution_set.shard_plan_id,
                "shard_plan_version": resolution_set.shard_plan_version,
                "shard_plan_digest": resolution_set.shard_plan_digest.value,
                "canonical_run_set_digest": resolution_set.canonical_run_set_digest.value,
                "batch_item_resolution_set_digest": resolution_set.resolution_set_digest.value,
                "item_bindings": item_bindings,
                "terminal_run_refs": [ref.canonical_payload() for ref in terminal_run_refs],
            },
        )

    @property
    def schema_version(self) -> str:
        return "qep.batch-finalization-basis.v1"

    def canonical_payload(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "source_batch_version": self.source_batch_version,
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest.value,
            "item_count": self.item_count,
            "shard_plan_id": self.shard_plan_id,
            "shard_plan_version": self.shard_plan_version,
            "shard_plan_digest": self.shard_plan_digest.value,
            "canonical_run_set_digest": self.canonical_run_set_digest.value,
            "terminal_run_refs": [ref.canonical_payload() for ref in self.terminal_run_refs],
            "non_run_refs": [ref.canonical_payload() for ref in self.non_run_refs],
            "success_policy_id": self.success_policy_id,
            "success_policy_version": self.success_policy_version,
            "success_policy_digest": self.success_policy_digest.value,
            "batch_item_resolution_schema": self.batch_item_resolution_schema,
            "batch_item_resolution_set_digest": self.batch_item_resolution_set_digest.value,
            "original_denominator": self.counts.original_denominator,
            "passed_count": self.counts.passed_count,
            "test_failed_count": self.counts.test_failed_count,
            "infra_failed_count": self.counts.infra_failed_count,
            "cancelled_count": self.counts.cancelled_count,
            "unknown_lineage_count": self.counts.unknown_lineage_count,
            "not_executed_count": self.counts.not_executed_count,
            "completeness_proof_digest": self.completeness_proof_digest.value,
            "batch_cancellation_intent_digest": _value(self.batch_cancellation_intent_digest),
            "unknown_fact_refs": [ref.canonical_payload() for ref in self.unknown_fact_refs],
            "batch_outcome": self.batch_outcome.value,
        }

    @property
    def basis_digest(self) -> Digest:
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
