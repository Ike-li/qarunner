"""V1 Run phase, disposition, and outcome cross-field values."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import DomainValidationError


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
            _forbid_present(entity, self, *retry_fields, "adjudication_digest")
        elif self.source_kind is EffectiveSourceKind.AUTHORIZED_RETRY:
            _require_attempt_identity(entity, self)
            for field in retry_fields:
                _require_digest(entity, field, getattr(self, field))
            if self.adjudication_digest is not None:
                _require_digest(entity, "adjudication_digest", self.adjudication_digest)
        else:
            _require_attempt_identity(entity, self)
            _forbid_present(entity, self, *retry_fields)
            _require_digest(entity, "adjudication_digest", self.adjudication_digest)
            if self.outcome is not RunOutcome.INFRA_FAILED:
                _require_digest(entity, "evidence_root_digest", self.evidence_root_digest)

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
