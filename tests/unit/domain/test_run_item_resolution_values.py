"""T-M0-STATE-001G G2a: immutable per-item resolution values."""

from dataclasses import replace

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(schema_version="qep.test-item-resolution.v1", payload={"label": label})


def _key():
    from qarunner.domain import RunItemKey

    return RunItemKey(manifest_id="manifest-001", item_index=7)


def _original(kind="ATTEMPT_RESULT", fact="PASSED", **changes):
    from qarunner.domain import AttemptExecutionFact, OriginalItemResolution, OriginalSourceKind

    values = {
        "source_kind": OriginalSourceKind[kind],
        "attempt_id": "attempt-001",
        "attempt_no": 1,
        "attempt_fence": 3,
        "attempt_item_set_digest": _digest("attempt-items"),
        "execution_fact": AttemptExecutionFact[fact],
        "fact_schema": "qep.case-result.v1",
        "fact_version": 1,
        "fact_digest": _digest("fact"),
        "evidence_root_digest": _digest("evidence"),
        "result_mapping_schema": None,
        "result_mapping_version": None,
        "result_mapping_digest": None,
        "prestart_closure_digest": None,
    }
    values.update(changes)
    return OriginalItemResolution(**values)


def _effective(kind="ORIGINAL", outcome="PASSED", **changes):
    from qarunner.domain import EffectiveItemResolution, EffectiveSourceKind, RunOutcome

    values = {
        "source_kind": EffectiveSourceKind[kind],
        "attempt_id": "attempt-001",
        "attempt_no": 1,
        "attempt_fence": 3,
        "attempt_item_set_digest": _digest("attempt-items"),
        "outcome": RunOutcome[outcome],
        "fact_schema": "qep.case-result.v1",
        "fact_version": 1,
        "fact_digest": _digest("fact"),
        "evidence_root_digest": _digest("evidence"),
        "result_mapping_schema": None,
        "result_mapping_version": None,
        "result_mapping_digest": None,
        "retry_intent_digest": None,
        "retry_decision_digest": None,
        "retry_authority_digest": None,
        "adjudication_digest": None,
    }
    values.update(changes)
    return EffectiveItemResolution(**values)


def test_vocabularies_and_item_key_order_are_exact() -> None:
    from qarunner.domain import (
        AttemptExecutionFact,
        EffectiveSourceKind,
        ItemAggregationClass,
        OriginalSourceKind,
        RunItemKey,
    )

    assert tuple(x.value for x in OriginalSourceKind) == (
        "attempt_result",
        "attempt_terminal_fallback",
        "prestart_cancel",
    )
    assert tuple(x.value for x in EffectiveSourceKind) == (
        "original",
        "authorized_retry",
        "unknown_adjudication",
    )
    assert tuple(x.value for x in AttemptExecutionFact) == (
        "passed",
        "test_failed",
        "infra_failed",
        "cancelled",
        "attempt_unknown",
    )
    assert tuple(x.value for x in ItemAggregationClass) == (
        "passed",
        "test_failed",
        "infra_failed",
        "cancelled",
        "unknown_lineage",
    )
    assert RunItemKey("a", 2) < RunItemKey("b", 0)
    assert RunItemKey("a", 1) < RunItemKey("a", 2)


@pytest.mark.parametrize(
    "changes", [{"manifest_id": ""}, {"item_index": -1}, {"item_index": True}]
)
def test_item_key_rejects_invalid_identity(changes) -> None:
    from qarunner.domain import RunItemKey

    values = {"manifest_id": "manifest-001", "item_index": 0} | changes
    with pytest.raises(ValueError):
        RunItemKey(**values)


def test_attempt_result_accepts_complete_mapping_and_has_stable_digest() -> None:
    original = _original(
        result_mapping_schema="qep.result-mapping.v1",
        result_mapping_version=2,
        result_mapping_digest=_digest("mapping"),
    )
    assert original.digest == replace(original).digest
    assert original.canonical_payload()["prestart_closure_digest"] is None


@pytest.mark.parametrize("fact", ["INFRA_FAILED", "CANCELLED", "ATTEMPT_UNKNOWN"])
def test_attempt_result_rejects_non_case_execution_facts(fact: str) -> None:
    with pytest.raises(ValueError, match="execution_fact"):
        _original(fact=fact)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": None},
        {"attempt_no": None},
        {"attempt_fence": None},
        {"attempt_item_set_digest": None},
        {"evidence_root_digest": None},
        {"prestart_closure_digest": _digest("closure")},
    ],
)
def test_attempt_result_enforces_required_and_forbidden_fields(changes) -> None:
    with pytest.raises(ValueError):
        _original(**changes)


@pytest.mark.parametrize(
    "missing", ["result_mapping_schema", "result_mapping_version", "result_mapping_digest"]
)
def test_mapping_identity_is_all_or_none(missing: str) -> None:
    changes = {
        "result_mapping_schema": "qep.result-mapping.v1",
        "result_mapping_version": 1,
        "result_mapping_digest": _digest("mapping"),
        missing: None,
    }
    with pytest.raises(ValueError, match="result_mapping"):
        _original(**changes)


@pytest.mark.parametrize("fact", ["INFRA_FAILED", "CANCELLED", "ATTEMPT_UNKNOWN"])
def test_terminal_fallback_accepts_only_terminal_facts_without_mapping(fact: str) -> None:
    value = _original(kind="ATTEMPT_TERMINAL_FALLBACK", fact=fact, evidence_root_digest=None)
    assert value.execution_fact.value == fact.lower()


@pytest.mark.parametrize("fact", ["PASSED", "TEST_FAILED"])
def test_terminal_fallback_rejects_case_facts(fact: str) -> None:
    with pytest.raises(ValueError, match="execution_fact"):
        _original(kind="ATTEMPT_TERMINAL_FALLBACK", fact=fact, evidence_root_digest=None)


def test_terminal_fallback_forbids_raw_result_mapping() -> None:
    with pytest.raises(ValueError, match="result_mapping"):
        _original(
            kind="ATTEMPT_TERMINAL_FALLBACK",
            fact="INFRA_FAILED",
            evidence_root_digest=None,
            result_mapping_schema="qep.mapping.v1",
            result_mapping_version=1,
            result_mapping_digest=_digest("mapping"),
        )


def test_prestart_cancel_has_no_attempt_or_evidence_identity() -> None:
    value = _original(
        kind="PRESTART_CANCEL",
        fact="CANCELLED",
        attempt_id=None,
        attempt_no=None,
        attempt_fence=None,
        attempt_item_set_digest=None,
        evidence_root_digest=None,
        prestart_closure_digest=_digest("closure"),
    )
    assert value.attempt_id is None


def test_prestart_cancel_effective_original_preserves_absent_attempt_identity() -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    original = _original(
        kind="PRESTART_CANCEL",
        fact="CANCELLED",
        attempt_id=None,
        attempt_no=None,
        attempt_fence=None,
        attempt_item_set_digest=None,
        evidence_root_digest=None,
        prestart_closure_digest=_digest("closure"),
    )
    effective = _effective(
        outcome="CANCELLED",
        attempt_id=None,
        attempt_no=None,
        attempt_fence=None,
        attempt_item_set_digest=None,
        evidence_root_digest=None,
    )

    value = RunItemResolution(_key(), original, effective, None, ItemAggregationClass.CANCELLED)

    assert value.effective.attempt_id is None


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_fact": "PASSED"},
        {"attempt_id": "attempt-001"},
        {"evidence_root_digest": _digest("evidence")},
        {"prestart_closure_digest": None},
    ],
)
def test_prestart_cancel_enforces_matrix(changes) -> None:
    from qarunner.domain import AttemptExecutionFact

    base = {
        "attempt_id": None,
        "attempt_no": None,
        "attempt_fence": None,
        "attempt_item_set_digest": None,
        "evidence_root_digest": None,
        "prestart_closure_digest": _digest("closure"),
    }
    if isinstance(changes.get("execution_fact"), str):
        changes["execution_fact"] = AttemptExecutionFact[changes["execution_fact"]]
    with pytest.raises(ValueError):
        _original(kind="PRESTART_CANCEL", fact="CANCELLED", **(base | changes))


def test_effective_original_must_exactly_mirror_concrete_original() -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    resolution = RunItemResolution(
        item_key=_key(),
        original=_original(),
        effective=_effective(),
        unknown_lineage_digest=None,
        aggregation_class=ItemAggregationClass.PASSED,
    )
    assert resolution.item_resolution_digest == replace(resolution).item_resolution_digest
    assert resolution.canonical_payload()["unknown_lineage_digest"] is None


def test_effective_original_rejects_retry_fields_and_mismatch() -> None:
    with pytest.raises(ValueError, match="retry"):
        _effective(retry_intent_digest=_digest("retry"))
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    with pytest.raises(ValueError, match="mirror"):
        RunItemResolution(
            _key(),
            _original(),
            _effective(fact_digest=_digest("other")),
            None,
            ItemAggregationClass.PASSED,
        )


def test_effective_original_attempt_identity_is_all_or_none() -> None:
    with pytest.raises(ValueError, match="attempt_identity"):
        _effective(attempt_id=None)


def test_authorized_retry_requires_complete_authority_and_new_fact() -> None:
    value = _effective(
        kind="AUTHORIZED_RETRY",
        attempt_id="attempt-002",
        attempt_no=2,
        attempt_fence=4,
        attempt_item_set_digest=_digest("retry-items"),
        fact_digest=_digest("retry-fact"),
        retry_intent_digest=_digest("retry-intent"),
        retry_decision_digest=_digest("retry-decision"),
        retry_authority_digest=_digest("retry-authority"),
    )
    assert value.source_kind.value == "authorized_retry"
    assert value.digest == replace(value).digest
    with pytest.raises(ValueError, match="retry_authority_digest"):
        replace(value, retry_authority_digest=None)


def test_unknown_authorized_retry_may_carry_adjudication_and_sticky_lineage() -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    effective = _effective(
        kind="AUTHORIZED_RETRY",
        attempt_id="attempt-002",
        attempt_no=2,
        attempt_fence=4,
        attempt_item_set_digest=_digest("retry-items"),
        fact_digest=_digest("retry-fact"),
        retry_intent_digest=_digest("retry-intent"),
        retry_decision_digest=_digest("decision"),
        retry_authority_digest=_digest("authority"),
        adjudication_digest=_digest("adjudication"),
    )
    value = RunItemResolution(
        _key(),
        _original(
            kind="ATTEMPT_TERMINAL_FALLBACK", fact="ATTEMPT_UNKNOWN", evidence_root_digest=None
        ),
        effective,
        _digest("unknown-lineage"),
        ItemAggregationClass.UNKNOWN_LINEAGE,
    )
    assert value.effective.outcome.value == "passed"


def test_unknown_adjudication_enforces_authority_and_outcome_evidence() -> None:
    value = _effective(
        kind="UNKNOWN_ADJUDICATION",
        outcome="INFRA_FAILED",
        evidence_root_digest=None,
        adjudication_digest=_digest("adjudication"),
    )
    assert value.outcome.value == "infra_failed"
    with pytest.raises(ValueError, match="evidence_root_digest"):
        replace(
            value, outcome=__import__("qarunner.domain", fromlist=["RunOutcome"]).RunOutcome.PASSED
        )
    with pytest.raises(ValueError, match="retry"):
        replace(value, retry_intent_digest=_digest("retry"))


def test_unadjudicated_unknown_cannot_construct_a_resolution() -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    unknown = _original(
        kind="ATTEMPT_TERMINAL_FALLBACK", fact="ATTEMPT_UNKNOWN", evidence_root_digest=None
    )
    with pytest.raises(ValueError, match="unadjudicated_unknown"):
        RunItemResolution(_key(), unknown, _effective(), None, ItemAggregationClass.PASSED)


@pytest.mark.parametrize("outcome", ["PASSED", "TEST_FAILED", "INFRA_FAILED", "CANCELLED"])
def test_aggregation_class_is_deterministic_without_unknown_lineage(outcome: str) -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    original = (
        _original(fact=outcome)
        if outcome in {"PASSED", "TEST_FAILED"}
        else _original(kind="ATTEMPT_TERMINAL_FALLBACK", fact=outcome, evidence_root_digest=None)
    )
    effective = _effective(
        outcome=outcome,
        evidence_root_digest=original.evidence_root_digest,
        fact_schema=original.fact_schema,
        fact_version=original.fact_version,
        fact_digest=original.fact_digest,
    )
    value = RunItemResolution(_key(), original, effective, None, ItemAggregationClass[outcome])
    assert value.aggregation_class.value == outcome.lower()
    with pytest.raises(ValueError, match="aggregation_class"):
        replace(value, aggregation_class=ItemAggregationClass.UNKNOWN_LINEAGE)


def test_unknown_lineage_requires_unknown_aggregation_and_resolved_unknown_origin() -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    with pytest.raises(ValueError, match="unknown_lineage"):
        RunItemResolution(
            _key(),
            _original(),
            _effective(),
            _digest("lineage"),
            ItemAggregationClass.UNKNOWN_LINEAGE,
        )
    with pytest.raises(ValueError, match="aggregation_class"):
        RunItemResolution(
            _key(), _original(), _effective(), None, ItemAggregationClass.UNKNOWN_LINEAGE
        )

    unknown = _original(
        kind="ATTEMPT_TERMINAL_FALLBACK", fact="ATTEMPT_UNKNOWN", evidence_root_digest=None
    )
    retry = _effective(
        kind="AUTHORIZED_RETRY",
        retry_intent_digest=_digest("intent"),
        retry_decision_digest=_digest("decision"),
        retry_authority_digest=_digest("authority"),
        adjudication_digest=_digest("adjudication"),
    )
    with pytest.raises(ValueError, match="required_for_resolved_unknown"):
        RunItemResolution(_key(), unknown, retry, None, ItemAggregationClass.PASSED)
    with pytest.raises(ValueError, match="unknown_lineage_required"):
        RunItemResolution(_key(), unknown, retry, _digest("lineage"), ItemAggregationClass.PASSED)

    later_unknown = RunItemResolution(
        _key(),
        _original(),
        retry,
        _digest("later-lineage"),
        ItemAggregationClass.UNKNOWN_LINEAGE,
    )
    assert later_unknown.unknown_lineage_digest == _digest("later-lineage")


@pytest.mark.parametrize("field", ["item_key", "original", "effective"])
def test_run_item_resolution_rejects_untyped_members(field: str) -> None:
    from qarunner.domain import ItemAggregationClass, RunItemResolution

    values = {
        "item_key": _key(),
        "original": _original(),
        "effective": _effective(),
        "unknown_lineage_digest": None,
        "aggregation_class": ItemAggregationClass.PASSED,
    }
    values[field] = object()
    with pytest.raises(ValueError, match=field):
        RunItemResolution(**values)


@pytest.mark.parametrize(
    "factory,changes",
    [
        (_original, {"fact_schema": ""}),
        (_original, {"fact_version": 0}),
        (_original, {"fact_digest": "not-a-digest"}),
        (_effective, {"source_kind": "original"}),
    ],
)
def test_values_reject_raw_or_malformed_identity(factory, changes) -> None:
    with pytest.raises(ValueError):
        factory(**changes)


def test_wide_attempt_lifecycle_enum_cannot_be_used_as_execution_fact() -> None:
    from qarunner.domain import AttemptState

    with pytest.raises(ValueError, match="execution_fact"):
        _original(execution_fact=AttemptState.RUNNING)
