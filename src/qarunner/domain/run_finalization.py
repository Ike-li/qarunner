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


class TerminalInputKind(enum.StrEnum):
    VERIFIED_EVIDENCE = "verified_evidence"
    VERIFIED_CANCELLATION_EVIDENCE = "verified_cancellation_evidence"
    PRESTART_CANCEL = "prestart_cancel"
    UNKNOWN_ADJUDICATION = "unknown_adjudication"


class FinalizationDecisionKind(enum.StrEnum):
    CONTRACT_RULE = "contract_rule"
    SUITE_RETRY = "suite_retry"
    PLATFORM_RETRY = "platform_retry"
    UNKNOWN_ADJUDICATION = "unknown_adjudication"
    DUPLICATE_RISK_ACCEPTANCE = "duplicate_risk_acceptance"


@dataclass(frozen=True, slots=True)
class ContractRuleResultRef:
    outcome: RunOutcome
    cancellation_intent_digest: Digest | None = None
    cancellation_stop_digest: Digest | None = None
    prestart_closure_digest: Digest | None = None

    def __post_init__(self) -> None:
        entity = "contract_rule_result_ref"
        _require_optional_enum(entity, "outcome", self.outcome, RunOutcome, required=True)
        for field in (
            "cancellation_intent_digest",
            "cancellation_stop_digest",
            "prestart_closure_digest",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_digest(entity, field, value)

    def canonical_payload(self) -> dict[str, object]:
        return _result_payload(self)


@dataclass(frozen=True, slots=True)
class RetryDecisionResultRef:
    retry_intent_digest: Digest
    retry_decision_digest: Digest
    retry_authority_digest: Digest
    target_attempt_id: str
    target_attempt_no: int
    target_attempt_fence: int
    target_item_set_digest: Digest

    def __post_init__(self) -> None:
        _require_digest(
            "retry_decision_result_ref", "retry_intent_digest", self.retry_intent_digest
        )
        _require_digest(
            "retry_decision_result_ref", "retry_decision_digest", self.retry_decision_digest
        )
        _require_digest(
            "retry_decision_result_ref", "retry_authority_digest", self.retry_authority_digest
        )
        _require_string("retry_decision_result_ref", "target_attempt_id", self.target_attempt_id)
        _require_positive_int(
            "retry_decision_result_ref", "target_attempt_no", self.target_attempt_no
        )
        _require_positive_int(
            "retry_decision_result_ref", "target_attempt_fence", self.target_attempt_fence
        )
        _require_digest(
            "retry_decision_result_ref", "target_item_set_digest", self.target_item_set_digest
        )

    def canonical_payload(self) -> dict[str, object]:
        return _result_payload(self)


@dataclass(frozen=True, slots=True)
class UnknownAdjudicationResultRef:
    adjudication_digest: Digest
    decision: UnknownAdjudicationDecision
    evidence_root_digest: Digest | None = None
    proof_digest: Digest | None = None
    risk_acceptance_digest: Digest | None = None

    def __post_init__(self) -> None:
        _require_digest(self.__class__.__name__, "adjudication_digest", self.adjudication_digest)
        if not isinstance(self.decision, UnknownAdjudicationDecision):
            _invalid(self.__class__.__name__, "decision", "unknown")
        for field in ("evidence_root_digest", "proof_digest", "risk_acceptance_digest"):
            value = getattr(self, field)
            if value is not None:
                _require_digest(self.__class__.__name__, field, value)

    def canonical_payload(self) -> dict[str, object]:
        return _result_payload(self)


@dataclass(frozen=True, slots=True)
class DuplicateRiskAcceptanceResultRef:
    risk_acceptance_digest: Digest

    def __post_init__(self) -> None:
        _require_digest(
            self.__class__.__name__, "risk_acceptance_digest", self.risk_acceptance_digest
        )

    def canonical_payload(self) -> dict[str, object]:
        return _result_payload(self)


@dataclass(frozen=True, slots=True)
class RunFinalizationDecisionRef:
    sequence: int
    decision_kind: FinalizationDecisionKind
    decision_schema: str
    decision_id: str
    decision_version: int
    decision_digest: Digest
    decision_result: (
        ContractRuleResultRef
        | RetryDecisionResultRef
        | UnknownAdjudicationResultRef
        | DuplicateRiskAcceptanceResultRef
    )
    source_attempt_id: str | None = None
    source_attempt_no: int | None = None
    source_attempt_fence: int | None = None
    source_item_set_digest: Digest | None = None

    def __post_init__(self) -> None:
        entity = "run_finalization_decision_ref"
        _require_positive_int(entity, "sequence", self.sequence)
        _require_optional_enum(
            entity, "decision_kind", self.decision_kind, FinalizationDecisionKind, required=True
        )
        _require_string(entity, "decision_schema", self.decision_schema)
        _require_string(entity, "decision_id", self.decision_id)
        _require_positive_int(entity, "decision_version", self.decision_version)
        _require_digest(entity, "decision_digest", self.decision_digest)
        expected = {
            FinalizationDecisionKind.CONTRACT_RULE: ContractRuleResultRef,
            FinalizationDecisionKind.SUITE_RETRY: RetryDecisionResultRef,
            FinalizationDecisionKind.PLATFORM_RETRY: RetryDecisionResultRef,
            FinalizationDecisionKind.UNKNOWN_ADJUDICATION: UnknownAdjudicationResultRef,
            FinalizationDecisionKind.DUPLICATE_RISK_ACCEPTANCE: DuplicateRiskAcceptanceResultRef,
        }
        if not isinstance(self.decision_result, expected[self.decision_kind]):
            _invalid(entity, "decision_result", "kind_mismatch")
        if isinstance(self.decision_result, RetryDecisionResultRef) and (
            self.decision_result.retry_decision_digest != self.decision_digest
        ):
            _invalid(entity, "decision_result", "decision_digest_mismatch")
        fields = (
            self.source_attempt_id,
            self.source_attempt_no,
            self.source_attempt_fence,
            self.source_item_set_digest,
        )
        if any(value is not None for value in fields) and not all(
            value is not None for value in fields
        ):
            _invalid(entity, "source_attempt", "all_or_none")
        if all(value is not None for value in fields):
            _require_string(entity, "source_attempt_id", self.source_attempt_id)
            _require_positive_int(entity, "source_attempt_no", self.source_attempt_no)
            _require_positive_int(entity, "source_attempt_fence", self.source_attempt_fence)
            _require_digest(entity, "source_item_set_digest", self.source_item_set_digest)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "decision_kind": self.decision_kind.value,
            "decision_schema": self.decision_schema,
            "decision_id": self.decision_id,
            "decision_version": self.decision_version,
            "decision_digest": self.decision_digest.value,
            "source_attempt_id": self.source_attempt_id,
            "source_attempt_no": self.source_attempt_no,
            "source_attempt_fence": self.source_attempt_fence,
            "source_item_set_digest": _digest_value(self.source_item_set_digest),
            "decision_result": self.decision_result.canonical_payload(),
        }


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
        ):
            _require_digest(entity, field, getattr(self, field))
        _require_digest(entity, "attempt_chain_digest", self.attempt_chain_digest)
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
class RunFinalizationBasis:
    run_id: str
    batch_id: str
    source_run_version: int
    manifest_digest: Digest
    shard_plan_digest: Digest
    run_item_set_digest: Digest
    execution_spec_digest: Digest
    original_attempt_id: str | None
    original_attempt_fence: int | None
    final_attempt_id: str | None
    final_attempt_no: int | None
    final_attempt_fence: int | None
    final_attempt_version: int | None
    final_attempt_state: AttemptExecutionFact | None
    worker_id: str | None
    worker_generation: int | None
    attempt_chain_digest: Digest | None
    evidence_root_digest: Digest | None
    unknown_observation_digest: Digest | None
    adjudication_chain_digest: Digest | None
    retry_chain_digest: Digest | None
    item_resolution_set_digest: Digest
    original_resolution_set_digest: Digest
    effective_resolution_set_digest: Digest
    item_count: int
    cancellation_intent_digest: Digest | None
    cancellation_stop_digest: Digest | None
    prestart_closure_digest: Digest | None
    terminal_rule_schema: str
    terminal_rule_version: int
    terminal_rule_digest: Digest
    decision_chain: tuple[RunFinalizationDecisionRef, ...]
    disposition: RunDisposition
    outcome: RunOutcome
    terminal_input_kind: TerminalInputKind

    def __post_init__(self) -> None:
        entity = "run_finalization_basis"
        for field in ("run_id", "batch_id", "terminal_rule_schema"):
            _require_string(entity, field, getattr(self, field))
        _require_nonnegative_int(entity, "source_run_version", self.source_run_version)
        _require_positive_int(entity, "terminal_rule_version", self.terminal_rule_version)
        _require_positive_int(entity, "item_count", self.item_count)
        for field in (
            "manifest_digest",
            "shard_plan_digest",
            "run_item_set_digest",
            "execution_spec_digest",
            "item_resolution_set_digest",
            "original_resolution_set_digest",
            "effective_resolution_set_digest",
            "terminal_rule_digest",
        ):
            _require_digest(entity, field, getattr(self, field))
        for field in (
            "evidence_root_digest",
            "unknown_observation_digest",
            "adjudication_chain_digest",
            "retry_chain_digest",
            "cancellation_intent_digest",
            "cancellation_stop_digest",
            "prestart_closure_digest",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_digest(entity, field, value)
        _require_optional_enum(
            entity, "disposition", self.disposition, RunDisposition, required=True
        )
        _require_optional_enum(entity, "outcome", self.outcome, RunOutcome, required=True)
        _require_optional_enum(
            entity,
            "terminal_input_kind",
            self.terminal_input_kind,
            TerminalInputKind,
            required=True,
        )
        if self.disposition is not RunDisposition.CLOSED_NO_RETRY:
            _invalid(entity, "disposition", "basis_requires_closed_no_retry")
        self._validate_decisions(entity)
        self._validate_input_kind(entity)

    @staticmethod
    def decision_ref(**values: object) -> RunFinalizationDecisionRef:
        return RunFinalizationDecisionRef(**values)  # type: ignore[arg-type]

    @classmethod
    def build(
        cls,
        *,
        resolution_set: RunItemResolutionSet,
        manifest_digest: Digest,
        shard_plan_digest: Digest,
        run_item_set_digest: Digest,
        source_run_version: int,
        **values: object,
    ) -> RunFinalizationBasis:
        if not isinstance(resolution_set, RunItemResolutionSet):
            _invalid("run_finalization_basis", "resolution_set", "invalid")
        expected = (
            resolution_set.manifest_digest,
            resolution_set.shard_plan_digest,
            resolution_set.run_item_set_digest,
            resolution_set.source_run_version,
        )
        if (
            manifest_digest,
            shard_plan_digest,
            run_item_set_digest,
            source_run_version,
        ) != expected:
            _invalid("run_finalization_basis", "resolution_set", "envelope_mismatch")
        return cls(
            run_id=resolution_set.run_id,
            batch_id=resolution_set.batch_id,
            source_run_version=source_run_version,
            manifest_digest=manifest_digest,
            shard_plan_digest=shard_plan_digest,
            run_item_set_digest=run_item_set_digest,
            attempt_chain_digest=(
                None
                if values.get("terminal_input_kind") is TerminalInputKind.PRESTART_CANCEL
                else resolution_set.attempt_chain_digest
            ),
            retry_chain_digest=resolution_set.retry_chain_digest,
            adjudication_chain_digest=resolution_set.adjudication_chain_digest,
            item_resolution_set_digest=resolution_set.resolution_set_digest,
            original_resolution_set_digest=resolution_set.original_resolution_set_digest,
            effective_resolution_set_digest=resolution_set.effective_resolution_set_digest,
            item_count=resolution_set.item_count,
            **values,
        )

    def _validate_decisions(self, entity: str) -> None:
        if not isinstance(self.decision_chain, tuple) or any(
            not isinstance(ref, RunFinalizationDecisionRef) for ref in self.decision_chain
        ):
            _invalid(entity, "decision_chain", "invalid")
        if tuple(ref.sequence for ref in self.decision_chain) != tuple(
            range(1, len(self.decision_chain) + 1)
        ):
            _invalid(entity, "decision_chain", "non_contiguous")
        if not any(
            ref.decision_kind is FinalizationDecisionKind.CONTRACT_RULE
            for ref in self.decision_chain
        ):
            _invalid(entity, "decision_chain", "contract_rule_required")
        self._validate_retry_decisions(entity)

    def _validate_retry_decisions(self, entity: str) -> None:
        retries = [
            ref
            for ref in self.decision_chain
            if ref.decision_kind
            in {FinalizationDecisionKind.SUITE_RETRY, FinalizationDecisionKind.PLATFORM_RETRY}
        ]
        previous_target: RetryDecisionResultRef | None = None
        for ref in retries:
            result = ref.decision_result
            assert isinstance(result, RetryDecisionResultRef)
            if ref.source_attempt_id is None:
                _invalid(entity, "decision_chain", "retry_source_required")
            if (
                result.target_attempt_no != ref.source_attempt_no + 1
                or result.target_attempt_fence <= ref.source_attempt_fence
                or result.target_item_set_digest != ref.source_item_set_digest
            ):
                _invalid(entity, "decision_chain", "retry_target_discontinuous")
            if previous_target is not None and (
                ref.source_attempt_id != previous_target.target_attempt_id
                or ref.source_attempt_no != previous_target.target_attempt_no
                or ref.source_attempt_fence != previous_target.target_attempt_fence
                or ref.source_item_set_digest != previous_target.target_item_set_digest
            ):
                _invalid(entity, "decision_chain", "retry_source_discontinuous")
            previous_target = result
        if retries:
            first = retries[0]
            last = previous_target
            assert last is not None
            if (
                first.source_attempt_id != self.original_attempt_id
                or first.source_attempt_fence != self.original_attempt_fence
                or last.target_attempt_id != self.final_attempt_id
                or last.target_attempt_no != self.final_attempt_no
                or last.target_attempt_fence != self.final_attempt_fence
                or last.target_item_set_digest != self.run_item_set_digest
            ):
                _invalid(entity, "decision_chain", "retry_basis_mismatch")

    def _validate_input_kind(self, entity: str) -> None:
        attempt_fields = (
            "original_attempt_id",
            "original_attempt_fence",
            "final_attempt_id",
            "final_attempt_no",
            "final_attempt_fence",
            "final_attempt_version",
            "final_attempt_state",
            "worker_id",
            "worker_generation",
        )
        if self.terminal_input_kind is TerminalInputKind.PRESTART_CANCEL:
            _forbid_present(
                entity, self, *attempt_fields, "attempt_chain_digest", "evidence_root_digest"
            )
            for field in ("cancellation_intent_digest", "prestart_closure_digest"):
                _require_digest(entity, field, getattr(self, field))
            if self.cancellation_stop_digest is not None:
                _invalid(entity, "cancellation_stop_digest", "forbidden")
            if self.outcome is not RunOutcome.CANCELLED:
                _invalid(entity, "outcome", "prestart_requires_cancelled")
            return
        _require_digest(entity, "attempt_chain_digest", self.attempt_chain_digest)
        for field in ("original_attempt_id", "final_attempt_id", "worker_id"):
            _require_string(entity, field, getattr(self, field))
        for field in (
            "original_attempt_fence",
            "final_attempt_no",
            "final_attempt_fence",
            "worker_generation",
        ):
            _require_positive_int(entity, field, getattr(self, field))
        _require_nonnegative_int(entity, "final_attempt_version", self.final_attempt_version)
        _require_optional_enum(
            entity,
            "final_attempt_state",
            self.final_attempt_state,
            AttemptExecutionFact,
            required=True,
        )
        if self.prestart_closure_digest is not None:
            _invalid(entity, "prestart_closure_digest", "forbidden")
        if self.terminal_input_kind is TerminalInputKind.VERIFIED_CANCELLATION_EVIDENCE:
            if self.final_attempt_state is not AttemptExecutionFact.CANCELLED:
                _invalid(entity, "final_attempt_state", "cancellation_requires_cancelled")
            for field in (
                "evidence_root_digest",
                "cancellation_intent_digest",
                "cancellation_stop_digest",
            ):
                _require_digest(entity, field, getattr(self, field))
            if self.outcome is not RunOutcome.CANCELLED:
                _invalid(entity, "outcome", "cancellation_requires_cancelled")
            self._validate_cancel_rule(entity)
        elif self.terminal_input_kind is TerminalInputKind.UNKNOWN_ADJUDICATION:
            if self.final_attempt_state is not AttemptExecutionFact.ATTEMPT_UNKNOWN:
                _invalid(entity, "final_attempt_state", "unknown_required")
            for field in ("unknown_observation_digest", "adjudication_chain_digest"):
                _require_digest(entity, field, getattr(self, field))
            refs = [
                ref.decision_result
                for ref in self.decision_chain
                if ref.decision_kind is FinalizationDecisionKind.UNKNOWN_ADJUDICATION
            ]
            if len(refs) != 1:
                _invalid(entity, "decision_chain", "unknown_adjudication_required")
            result = refs[0]
            assert isinstance(result, UnknownAdjudicationResultRef)
            if result.decision is UnknownAdjudicationDecision.MARK_INFRA_FAILED_NO_RETRY:
                if (
                    self.outcome is not RunOutcome.INFRA_FAILED
                    or self.evidence_root_digest is not None
                ):
                    _invalid(entity, "outcome", "infra_adjudication_matrix")
            elif (
                result.decision
                is UnknownAdjudicationDecision.MARK_COMPLETED_FROM_VERIFIED_EVIDENCE
            ):
                _require_digest(entity, "evidence_root_digest", self.evidence_root_digest)
                if result.evidence_root_digest != self.evidence_root_digest:
                    _invalid(entity, "evidence_root_digest", "adjudication_mismatch")
            else:
                _invalid(entity, "decision_result", "retry_cannot_close_unknown")
        else:
            if self.final_attempt_state is AttemptExecutionFact.ATTEMPT_UNKNOWN:
                _invalid(entity, "final_attempt_state", "unresolved_unknown")
            _require_digest(entity, "evidence_root_digest", self.evidence_root_digest)
            if self.outcome.value != self.final_attempt_state.value:
                _invalid(entity, "outcome", "attempt_state_mismatch")

    def _validate_cancel_rule(self, entity: str) -> None:
        rules = [
            ref.decision_result
            for ref in self.decision_chain
            if ref.decision_kind is FinalizationDecisionKind.CONTRACT_RULE
        ]
        if not any(
            isinstance(rule, ContractRuleResultRef)
            and rule.cancellation_intent_digest == self.cancellation_intent_digest
            and rule.cancellation_stop_digest == self.cancellation_stop_digest
            for rule in rules
        ):
            _invalid(entity, "decision_chain", "cancellation_rule_mismatch")

    def canonical_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"schema_version": "qep.run-finalization-basis.v1"}
        for field in self.__dataclass_fields__:
            value = getattr(self, field)
            if isinstance(value, (Digest, enum.StrEnum)):
                payload[field] = value.value
            elif isinstance(value, tuple):
                payload[field] = [item.canonical_payload() for item in value]
            else:
                payload[field] = value
        payload["item_resolution_schema"] = "qep.run-item-resolution-set"
        payload["item_resolution_version"] = 1
        payload["decision_chain_digest"] = canonical_digest(
            schema_version="qep.run-finalization-decision-chain.v1",
            payload={"decisions": payload["decision_chain"]},
        ).value
        return payload

    @property
    def basis_digest(self) -> Digest:
        return canonical_digest(
            schema_version="qep.run-finalization-basis.v1", payload=self.canonical_payload()
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


def _result_payload(value: object) -> dict[str, object]:
    return {
        field: (item.value if isinstance(item, (Digest, enum.StrEnum)) else item)
        for field in value.__dataclass_fields__
        for item in (getattr(value, field),)
    }


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
