"""V1 Run phase, disposition, and outcome cross-field values."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import AttemptUnknownReviewRequired, DomainValidationError
from qarunner.domain.unknown import UnknownAdjudicationDecision


class RunPhase(enum.StrEnum):
    """Orchestration lifecycle, independent from execution outcome."""

    PLANNED = "planned"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    RETRY_QUEUED = "retry_queued"
    CLOSED = "closed"


class RunDisposition(enum.StrEnum):
    """Policy handling state for the latest authoritative execution fact."""

    REVIEW_REQUIRED = "review_required"
    RETRY_QUEUED = "retry_queued"
    CLOSED_NO_RETRY = "closed_no_retry"


class RunOutcome(enum.StrEnum):
    """Persisted policy outcome available only for a closed Run."""

    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"


class OriginalSourceKind(enum.StrEnum):
    ATTEMPT_RESULT = "attempt_result"
    ATTEMPT_TERMINAL_FALLBACK = "attempt_terminal_fallback"
    PRESTART_CANCEL = "prestart_cancel"


class EffectiveSourceKind(enum.StrEnum):
    ORIGINAL = "original"
    AUTHORIZED_RETRY = "authorized_retry"
    UNKNOWN_ADJUDICATION = "unknown_adjudication"


class AttemptExecutionFact(enum.StrEnum):
    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"
    ATTEMPT_UNKNOWN = "attempt_unknown"


class ItemAggregationClass(enum.StrEnum):
    PASSED = "passed"
    TEST_FAILED = "test_failed"
    INFRA_FAILED = "infra_failed"
    CANCELLED = "cancelled"
    UNKNOWN_LINEAGE = "unknown_lineage"


@dataclass(frozen=True, slots=True, order=True)
class RunItemKey:
    manifest_id: str
    item_index: int

    def __post_init__(self) -> None:
        _require_string("run_item_key", "manifest_id", self.manifest_id)
        if (
            isinstance(self.item_index, bool)
            or not isinstance(self.item_index, int)
            or self.item_index < 0
        ):
            _invalid("run_item_key", "item_index", "invalid")

    def canonical_payload(self) -> dict[str, str | int]:
        return {"manifest_id": self.manifest_id, "item_index": self.item_index}


@dataclass(frozen=True, slots=True)
class OriginalItemResolution:
    source_kind: OriginalSourceKind
    attempt_id: str | None
    attempt_no: int | None
    attempt_fence: int | None
    attempt_item_set_digest: Digest | None
    execution_fact: AttemptExecutionFact
    fact_schema: str
    fact_version: int
    fact_digest: Digest
    evidence_root_digest: Digest | None
    result_mapping_schema: str | None
    result_mapping_version: int | None
    result_mapping_digest: Digest | None
    prestart_closure_digest: Digest | None

    def __post_init__(self) -> None:
        entity = "original_item_resolution"
        _require_optional_enum(
            entity, "source_kind", self.source_kind, OriginalSourceKind, required=True
        )
        _require_optional_enum(
            entity, "execution_fact", self.execution_fact, AttemptExecutionFact, required=True
        )
        _validate_fact_and_mapping(self, entity)
        if self.source_kind is OriginalSourceKind.PRESTART_CANCEL:
            if self.execution_fact is not AttemptExecutionFact.CANCELLED:
                _invalid(entity, "execution_fact", "prestart_requires_cancelled")
            _forbid_present(
                entity,
                self,
                "attempt_id",
                "attempt_no",
                "attempt_fence",
                "attempt_item_set_digest",
                "evidence_root_digest",
                "result_mapping_schema",
                "result_mapping_version",
                "result_mapping_digest",
            )
            _require_digest(entity, "prestart_closure_digest", self.prestart_closure_digest)
            return
        _require_attempt_identity(entity, self)
        if self.prestart_closure_digest is not None:
            _invalid(entity, "prestart_closure_digest", "forbidden")
        if self.source_kind is OriginalSourceKind.ATTEMPT_RESULT:
            if self.execution_fact not in {
                AttemptExecutionFact.PASSED,
                AttemptExecutionFact.TEST_FAILED,
            }:
                _invalid(entity, "execution_fact", "invalid_for_attempt_result")
            _require_digest(entity, "evidence_root_digest", self.evidence_root_digest)
        else:
            if self.execution_fact not in {
                AttemptExecutionFact.INFRA_FAILED,
                AttemptExecutionFact.CANCELLED,
                AttemptExecutionFact.ATTEMPT_UNKNOWN,
            }:
                _invalid(entity, "execution_fact", "invalid_for_terminal_fallback")
            if self.result_mapping_digest is not None:
                _invalid(entity, "result_mapping", "forbidden_for_terminal_fallback")

    def canonical_payload(self) -> dict[str, object]:
        return _resolution_payload(self, execution_field="execution_fact")

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-item-resolution-set.v1",
            payload={"projection_kind": "original", **self.canonical_payload()},
        )


@dataclass(frozen=True, slots=True)
class EffectiveItemResolution:
    source_kind: EffectiveSourceKind
    attempt_id: str | None
    attempt_no: int | None
    attempt_fence: int | None
    attempt_item_set_digest: Digest | None
    outcome: RunOutcome
    fact_schema: str
    fact_version: int
    fact_digest: Digest
    evidence_root_digest: Digest | None
    result_mapping_schema: str | None
    result_mapping_version: int | None
    result_mapping_digest: Digest | None
    retry_intent_digest: Digest | None
    retry_decision_digest: Digest | None
    retry_authority_digest: Digest | None
    adjudication_digest: Digest | None
    adjudication_decision: UnknownAdjudicationDecision | None = None

    def __post_init__(self) -> None:
        entity = "effective_item_resolution"
        _require_optional_enum(
            entity, "source_kind", self.source_kind, EffectiveSourceKind, required=True
        )
        _require_optional_enum(entity, "outcome", self.outcome, RunOutcome, required=True)
        _validate_fact_and_mapping(self, entity)
        retry_fields = ("retry_intent_digest", "retry_decision_digest", "retry_authority_digest")
        if self.source_kind is EffectiveSourceKind.ORIGINAL:
            _validate_optional_attempt_identity(entity, self)
            _forbid_present(
                entity, self, *retry_fields, "adjudication_digest", "adjudication_decision"
            )
        elif self.source_kind is EffectiveSourceKind.AUTHORIZED_RETRY:
            _require_attempt_identity(entity, self)
            for field in retry_fields:
                _require_digest(entity, field, getattr(self, field))
            if (self.adjudication_digest is None) != (self.adjudication_decision is None):
                _invalid(entity, "adjudication_digest", "decision_all_or_none")
            if self.adjudication_digest is not None:
                _require_digest(entity, "adjudication_digest", self.adjudication_digest)
                if (
                    not isinstance(self.adjudication_decision, UnknownAdjudicationDecision)
                    or not self.adjudication_decision.permits_retry
                ):
                    _invalid(entity, "adjudication_decision", "does_not_permit_retry")
        else:
            _require_attempt_identity(entity, self)
            _forbid_present(entity, self, *retry_fields)
            _require_digest(entity, "adjudication_digest", self.adjudication_digest)
            if (
                self.adjudication_decision
                is UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY
            ):
                if self.outcome is not RunOutcome.INFRA_FAILED:
                    _invalid(entity, "outcome", "infra_adjudication_requires_infra")
                _forbid_present(
                    entity,
                    self,
                    "evidence_root_digest",
                    "result_mapping_schema",
                    "result_mapping_version",
                    "result_mapping_digest",
                )
            elif (
                self.adjudication_decision
                is UnknownAdjudicationDecision.MARK_COMPLETED_FROM_VERIFIED_EVIDENCE
            ):
                _require_digest(entity, "evidence_root_digest", self.evidence_root_digest)
            else:
                _invalid(entity, "adjudication_decision", "invalid_for_resolution")

    def canonical_payload(self) -> dict[str, object]:
        payload = _resolution_payload(self, execution_field="outcome")
        payload.update(
            {
                "retry_intent_digest": _digest_value(self.retry_intent_digest),
                "retry_decision_digest": _digest_value(self.retry_decision_digest),
                "retry_authority_digest": _digest_value(self.retry_authority_digest),
                "adjudication_digest": _digest_value(self.adjudication_digest),
            }
        )
        return payload

    @property
    def digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-item-resolution-set.v1",
            payload={"projection_kind": "effective", **self.canonical_payload()},
        )


@dataclass(frozen=True, slots=True)
class RunItemResolution:
    item_key: RunItemKey
    original: OriginalItemResolution
    effective: EffectiveItemResolution
    unknown_lineage_digest: Digest | None
    aggregation_class: ItemAggregationClass

    def __post_init__(self) -> None:
        entity = "run_item_resolution"
        if not isinstance(self.item_key, RunItemKey):
            _invalid(entity, "item_key", "invalid")
        if not isinstance(self.original, OriginalItemResolution):
            _invalid(entity, "original", "invalid")
        if not isinstance(self.effective, EffectiveItemResolution):
            _invalid(entity, "effective", "invalid")
        _require_optional_enum(
            entity,
            "aggregation_class",
            self.aggregation_class,
            ItemAggregationClass,
            required=True,
        )
        original_unknown = self.original.execution_fact is AttemptExecutionFact.ATTEMPT_UNKNOWN
        if self.effective.source_kind is EffectiveSourceKind.ORIGINAL:
            if original_unknown:
                _invalid(entity, "effective", "unadjudicated_unknown")
            if not _effective_mirrors_original(self.original, self.effective):
                _invalid(entity, "effective", "does_not_mirror_original")
        if self.unknown_lineage_digest is None:
            expected = ItemAggregationClass(self.effective.outcome.value)
            if self.aggregation_class is not expected:
                _invalid(entity, "aggregation_class", "does_not_match_outcome")
            if original_unknown:
                _invalid(entity, "unknown_lineage_digest", "required_for_resolved_unknown")
        else:
            _require_digest(entity, "unknown_lineage_digest", self.unknown_lineage_digest)
            if self.effective.adjudication_digest is None:
                _invalid(entity, "unknown_lineage_digest", "requires_adjudication")
            if self.aggregation_class is not ItemAggregationClass.UNKNOWN_LINEAGE:
                _invalid(entity, "aggregation_class", "unknown_lineage_required")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "item_key": self.item_key.canonical_payload(),
            "original": self.original.canonical_payload(),
            "effective": self.effective.canonical_payload(),
            "unknown_lineage_digest": _digest_value(self.unknown_lineage_digest),
            "aggregation_class": self.aggregation_class.value,
        }

    @property
    def item_resolution_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-item-resolution-set.v1",
            payload={"projection_kind": "item", **self.canonical_payload()},
        )


@dataclass(frozen=True, slots=True)
class RunItemResolutionSet:
    """Closed Run's canonical, complete, ordered per-item resolution basis."""

    batch_id: str
    run_id: str
    source_run_version: int
    manifest_digest: Digest
    shard_plan_digest: Digest
    run_item_set_digest: Digest
    attempt_chain_digest: Digest
    retry_chain_digest: Digest | None
    adjudication_chain_digest: Digest | None
    entries: tuple[RunItemResolution, ...]

    def __post_init__(self) -> None:
        entity = "run_item_resolution_set"
        _require_string(entity, "batch_id", self.batch_id)
        _require_string(entity, "run_id", self.run_id)
        _require_nonnegative_int(entity, "source_run_version", self.source_run_version)
        for field in (
            "manifest_digest",
            "shard_plan_digest",
            "run_item_set_digest",
            "attempt_chain_digest",
        ):
            _require_digest(entity, field, getattr(self, field))
        for field in ("retry_chain_digest", "adjudication_chain_digest"):
            value = getattr(self, field)
            if value is not None:
                _require_digest(entity, field, value)
        if not isinstance(self.entries, tuple) or not self.entries:
            _invalid(entity, "entries", "invalid")
        if any(not isinstance(entry, RunItemResolution) for entry in self.entries):
            _invalid(entity, "entries", "invalid")
        keys = tuple(entry.item_key for entry in self.entries)
        if len(set(keys)) != len(keys):
            _invalid(entity, "entries", "duplicate_key")
        if keys != tuple(sorted(keys)):
            _invalid(entity, "entries", "not_ordered")

    @classmethod
    def build(
        cls,
        *,
        expected_item_keys: tuple[RunItemKey, ...],
        entries: tuple[RunItemResolution, ...],
        **envelope: object,
    ) -> RunItemResolutionSet:
        if not isinstance(expected_item_keys, tuple) or any(
            not isinstance(key, RunItemKey) for key in expected_item_keys
        ):
            _invalid("run_item_resolution_set", "expected_item_keys", "invalid")
        if not isinstance(entries, tuple) or any(
            not isinstance(entry, RunItemResolution) for entry in entries
        ):
            _invalid("run_item_resolution_set", "entries", "invalid")
        entry_keys = tuple(entry.item_key for entry in entries)
        if len(set(entry_keys)) != len(entry_keys):
            _invalid("run_item_resolution_set", "entries", "duplicate_key")
        if set(entry_keys) != set(expected_item_keys) or len(expected_item_keys) != len(
            set(expected_item_keys)
        ):
            _invalid("run_item_resolution_set", "entries", "key_set_mismatch")
        return cls(entries=tuple(sorted(entries, key=lambda entry: entry.item_key)), **envelope)

    @property
    def item_count(self) -> int:
        return len(self.entries)

    def _envelope_payload(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "run_id": self.run_id,
            "source_run_version": self.source_run_version,
            "manifest_digest": self.manifest_digest.value,
            "shard_plan_digest": self.shard_plan_digest.value,
            "run_item_set_digest": self.run_item_set_digest.value,
        }

    @property
    def original_resolution_set_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-item-resolution-set.v1",
            payload={
                **self._envelope_payload(),
                "projection_kind": "original_set",
                "entries": [
                    {
                        "item_key": entry.item_key.canonical_payload(),
                        "original": entry.original.canonical_payload(),
                    }
                    for entry in self.entries
                ],
            },
        )

    @property
    def effective_resolution_set_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-item-resolution-set.v1",
            payload={
                **self._envelope_payload(),
                "projection_kind": "effective_set",
                "entries": [
                    {
                        "item_key": entry.item_key.canonical_payload(),
                        "effective": entry.effective.canonical_payload(),
                    }
                    for entry in self.entries
                ],
            },
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            **self._envelope_payload(),
            "attempt_chain_digest": self.attempt_chain_digest.value,
            "retry_chain_digest": _digest_value(self.retry_chain_digest),
            "adjudication_chain_digest": _digest_value(self.adjudication_chain_digest),
            "entries": [
                {
                    **entry.canonical_payload(),
                    "item_resolution_digest": entry.item_resolution_digest.value,
                }
                for entry in self.entries
            ],
            "item_count": self.item_count,
            "original_resolution_set_digest": self.original_resolution_set_digest.value,
            "effective_resolution_set_digest": self.effective_resolution_set_digest.value,
        }

    @property
    def resolution_set_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-item-resolution-set.v1", payload=self.canonical_payload()
        )

    @property
    def audit_outcome(self) -> RunOutcome:
        precedence = {
            RunOutcome.PASSED: 0,
            RunOutcome.CANCELLED: 1,
            RunOutcome.TEST_FAILED: 2,
            RunOutcome.INFRA_FAILED: 3,
        }
        return max((entry.effective.outcome for entry in self.entries), key=precedence.__getitem__)


@dataclass(frozen=True, slots=True)
class VerifiedAttemptItemResolution:
    item_key: RunItemKey
    effective: EffectiveItemResolution

    def __post_init__(self) -> None:
        if not isinstance(self.item_key, RunItemKey):
            _invalid("verified_attempt_item_resolution", "item_key", "invalid")
        if not isinstance(self.effective, EffectiveItemResolution):
            _invalid("verified_attempt_item_resolution", "effective", "invalid")


@dataclass(frozen=True, slots=True)
class VerifiedAttemptResolutionSet:
    """Verified observations for one full-Run authorized Attempt target."""

    attempt_id: str
    attempt_no: int
    attempt_fence: int
    attempt_item_set_digest: Digest
    items: tuple[VerifiedAttemptItemResolution, ...]

    def __post_init__(self) -> None:
        entity = "verified_attempt_resolution_set"
        _require_string(entity, "attempt_id", self.attempt_id)
        _require_positive_int(entity, "attempt_no", self.attempt_no)
        _require_positive_int(entity, "attempt_fence", self.attempt_fence)
        _require_digest(entity, "attempt_item_set_digest", self.attempt_item_set_digest)
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, VerifiedAttemptItemResolution) for item in self.items
        ):
            _invalid(entity, "items", "invalid")
        keys = tuple(item.item_key for item in self.items)
        if len(keys) != len(set(keys)):
            _invalid(entity, "items", "duplicate_key")
        for item in self.items:
            effective = item.effective
            if effective.source_kind is not EffectiveSourceKind.AUTHORIZED_RETRY:
                _invalid(entity, "items", "not_authorized_retry")
            if (
                effective.attempt_id != self.attempt_id
                or effective.attempt_no != self.attempt_no
                or effective.attempt_fence != self.attempt_fence
                or effective.attempt_item_set_digest != self.attempt_item_set_digest
            ):
                _invalid(entity, "items", "attempt_identity_mismatch")
        authority_triples = {
            (
                item.effective.retry_intent_digest,
                item.effective.retry_decision_digest,
                item.effective.retry_authority_digest,
            )
            for item in self.items
        }
        if len(authority_triples) > 1:
            _invalid(entity, "items", "retry_authority_mismatch")


def select_latest_authorized_complete_attempt(
    *,
    expected_item_keys: tuple[RunItemKey, ...],
    run_item_set_digest: Digest,
    original_attempt_no: int,
    original_attempt_fence: int,
    attempts: tuple[VerifiedAttemptResolutionSet, ...],
    run_id: str,
) -> VerifiedAttemptResolutionSet:
    """Validate a continuous full-Run authority chain and select its latest complete fact set."""
    entity = "authorized_retry_selection"
    if (
        not isinstance(expected_item_keys, tuple)
        or not expected_item_keys
        or any(not isinstance(key, RunItemKey) for key in expected_item_keys)
        or len(set(expected_item_keys)) != len(expected_item_keys)
    ):
        _invalid(entity, "expected_item_keys", "invalid")
    _require_string(entity, "run_id", run_id)
    _require_digest(entity, "run_item_set_digest", run_item_set_digest)
    _require_positive_int(entity, "original_attempt_no", original_attempt_no)
    _require_positive_int(entity, "original_attempt_fence", original_attempt_fence)
    if not isinstance(attempts, tuple) or not attempts:
        _invalid(entity, "attempts", "invalid")
    expected_no = original_attempt_no + 1
    previous_fence = original_attempt_fence
    complete: list[VerifiedAttemptResolutionSet] = []
    expected_keys = set(expected_item_keys)
    for attempt in attempts:
        if not isinstance(attempt, VerifiedAttemptResolutionSet):
            _invalid(entity, "attempts", "invalid")
        if attempt.attempt_no != expected_no or attempt.attempt_fence <= previous_fence:
            _invalid(entity, "attempts", "continuous_chain_required")
        if attempt.attempt_item_set_digest != run_item_set_digest:
            _invalid(entity, "attempt_item_set_digest", "full_run_item_set_required")
        item_keys = {item.item_key for item in attempt.items}
        if not item_keys <= expected_keys:
            _invalid(entity, "items", "cross_run_item")
        if item_keys == expected_keys:
            complete.append(attempt)
        expected_no += 1
        previous_fence = attempt.attempt_fence
    if not complete:
        latest = attempts[-1]
        raise AttemptUnknownReviewRequired(
            run_id=run_id,
            attempt_id=latest.attempt_id,
            fence=latest.attempt_fence,
            reason="no_authorized_complete_attempt",
        )
    return complete[-1]


@dataclass(frozen=True, slots=True)
class RunFinalizationState:
    """Pure v1 cross-field projection without changing the legacy Run writer."""

    phase: RunPhase
    disposition: RunDisposition | None
    outcome: RunOutcome | None
    finalization_basis_digest: Digest | None
    latest_attempt_fact: AttemptExecutionFact | None
    current_assignment_id: str | None
    pending_retry_intent_id: str | None

    def __post_init__(self) -> None:
        entity = "run_finalization_state"
        _require_optional_enum(entity, "phase", self.phase, RunPhase, required=True)
        _require_optional_enum(entity, "disposition", self.disposition, RunDisposition)
        _require_optional_enum(entity, "outcome", self.outcome, RunOutcome)
        _require_optional_enum(
            entity,
            "latest_attempt_fact",
            self.latest_attempt_fact,
            AttemptExecutionFact,
        )
        if self.finalization_basis_digest is not None and not isinstance(
            self.finalization_basis_digest, Digest
        ):
            _invalid(entity, "finalization_basis_digest", "not_digest")
        for field in ("current_assignment_id", "pending_retry_intent_id"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                _invalid(entity, field, "invalid")
        self._validate_closed(entity)
        self._validate_review(entity)
        self._validate_retry(entity)

    def _validate_closed(self, entity: str) -> None:
        if self.phase is RunPhase.CLOSED:
            if self.disposition is not RunDisposition.CLOSED_NO_RETRY:
                _invalid(entity, "disposition", "closed_requires_disposition")
            if self.outcome is None:
                _invalid(entity, "outcome", "closed_requires_outcome")
            if self.finalization_basis_digest is None:
                _invalid(entity, "finalization_basis_digest", "closed_requires_basis")
            if self.current_assignment_id is not None or self.pending_retry_intent_id is not None:
                _invalid(entity, "phase", "closed_forbids_active_pointer")
            return
        if self.disposition is RunDisposition.CLOSED_NO_RETRY:
            _invalid(entity, "disposition", "closed_disposition_requires_closed_phase")
        if self.outcome is not None:
            _invalid(entity, "outcome", "outcome_requires_closed")
        if self.finalization_basis_digest is not None:
            _invalid(entity, "finalization_basis_digest", "basis_requires_closed")

    def _validate_review(self, entity: str) -> None:
        if self.disposition is not RunDisposition.REVIEW_REQUIRED:
            return
        if self.phase is not RunPhase.RUNNING:
            _invalid(entity, "disposition", "review_requires_running")
        if self.latest_attempt_fact is not AttemptExecutionFact.ATTEMPT_UNKNOWN:
            _invalid(entity, "latest_attempt_fact", "review_requires_unknown")

    def _validate_retry(self, entity: str) -> None:
        if self.phase is RunPhase.RETRY_QUEUED:
            if self.disposition is not RunDisposition.RETRY_QUEUED:
                _invalid(entity, "disposition", "retry_requires_disposition")
            if self.pending_retry_intent_id is None:
                _invalid(
                    entity,
                    "pending_retry_intent_id",
                    "retry_requires_pending_intent",
                )
            if self.current_assignment_id is not None:
                _invalid(entity, "current_assignment_id", "retry_forbids_current_assignment")
            return
        if self.disposition is RunDisposition.RETRY_QUEUED:
            _invalid(entity, "disposition", "retry_disposition_requires_retry_phase")
        if self.pending_retry_intent_id is not None:
            _invalid(
                entity,
                "pending_retry_intent_id",
                "pending_intent_requires_retry_phase",
            )


def _require_optional_enum(
    entity: str,
    field: str,
    value: object,
    expected_type: type[enum.StrEnum],
    *,
    required: bool = False,
) -> None:
    if value is None and not required:
        return
    if not isinstance(value, expected_type):
        _invalid(entity, field, "unknown")


def _require_string(entity: str, field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        _invalid(entity, field, "invalid")


def _require_positive_int(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _invalid(entity, field, "invalid")


def _require_nonnegative_int(entity: str, field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(entity, field, "invalid")


def _require_digest(entity: str, field: str, value: object) -> None:
    if not isinstance(value, Digest):
        _invalid(entity, field, "not_digest")


def _forbid_present(entity: str, value: object, *fields: str) -> None:
    for field in fields:
        if getattr(value, field) is not None:
            _invalid(entity, field, "forbidden")


def _require_attempt_identity(entity: str, value: object) -> None:
    _require_string(entity, "attempt_id", value.attempt_id)
    _require_positive_int(entity, "attempt_no", value.attempt_no)
    _require_positive_int(entity, "attempt_fence", value.attempt_fence)
    _require_digest(entity, "attempt_item_set_digest", value.attempt_item_set_digest)


def _validate_optional_attempt_identity(entity: str, value: object) -> None:
    fields = ("attempt_id", "attempt_no", "attempt_fence", "attempt_item_set_digest")
    present = tuple(getattr(value, field) is not None for field in fields)
    if any(present) and not all(present):
        _invalid(entity, "attempt_identity", "all_or_none")
    if all(present):
        _require_attempt_identity(entity, value)


def _validate_fact_and_mapping(value: object, entity: str) -> None:
    _require_string(entity, "fact_schema", value.fact_schema)
    _require_positive_int(entity, "fact_version", value.fact_version)
    _require_digest(entity, "fact_digest", value.fact_digest)
    evidence = value.evidence_root_digest
    if evidence is not None:
        _require_digest(entity, "evidence_root_digest", evidence)
    fields = ("result_mapping_schema", "result_mapping_version", "result_mapping_digest")
    present = tuple(getattr(value, field) is not None for field in fields)
    if any(present) and not all(present):
        _invalid(entity, "result_mapping", "all_or_none")
    if all(present):
        _require_string(entity, fields[0], getattr(value, fields[0]))
        _require_positive_int(entity, fields[1], getattr(value, fields[1]))
        _require_digest(entity, fields[2], getattr(value, fields[2]))


def _digest_value(value: Digest | None) -> str | None:
    return None if value is None else value.value


def _resolution_payload(value: object, *, execution_field: str) -> dict[str, object]:
    execution = getattr(value, execution_field)
    return {
        "source_kind": value.source_kind.value,
        "attempt_id": value.attempt_id,
        "attempt_no": value.attempt_no,
        "attempt_fence": value.attempt_fence,
        "attempt_item_set_digest": _digest_value(value.attempt_item_set_digest),
        execution_field: execution.value,
        "fact_schema": value.fact_schema,
        "fact_version": value.fact_version,
        "fact_digest": value.fact_digest.value,
        "evidence_root_digest": _digest_value(value.evidence_root_digest),
        "result_mapping_schema": value.result_mapping_schema,
        "result_mapping_version": value.result_mapping_version,
        "result_mapping_digest": _digest_value(value.result_mapping_digest),
        **(
            {"prestart_closure_digest": _digest_value(value.prestart_closure_digest)}
            if execution_field == "execution_fact"
            else {}
        ),
    }


def _effective_mirrors_original(
    original: OriginalItemResolution,
    effective: EffectiveItemResolution,
) -> bool:
    expected_outcome = RunOutcome(original.execution_fact.value)
    return (
        effective.outcome is expected_outcome
        and effective.attempt_id == original.attempt_id
        and effective.attempt_no == original.attempt_no
        and effective.attempt_fence == original.attempt_fence
        and effective.attempt_item_set_digest == original.attempt_item_set_digest
        and effective.fact_schema == original.fact_schema
        and effective.fact_version == original.fact_version
        and effective.fact_digest == original.fact_digest
        and effective.evidence_root_digest == original.evidence_root_digest
        and effective.result_mapping_schema == original.result_mapping_schema
        and effective.result_mapping_version == original.result_mapping_version
        and effective.result_mapping_digest == original.result_mapping_digest
    )


def _invalid(entity: str, field: str, reason: str) -> None:
    raise DomainValidationError(entity_type=entity, field=field, reason=reason)
