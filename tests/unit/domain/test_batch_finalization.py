"""T-M0-STATE-001H H1: Batch policy and deterministic truth table."""

from dataclasses import replace
from itertools import product

import pytest

from qarunner.domain import (
    BatchOutcomeEvaluation,
    BatchResolutionCounts,
    BatchState,
    BatchSuccessPolicy,
    DomainValidationError,
    canonical_digest,
    evaluate_batch_outcome,
)


def _digest(label: str):
    return canonical_digest(schema_version="qep.test-batch-finalization.v1", payload={"v": label})


def _policy(**changes):
    values = {
        "schema_version": "qep.batch-success-policy.v1",
        "policy_id": "policy-001",
        "policy_version": 1,
        "suite_id": "suite-001",
        "max_test_failed_items": 1,
        "allow_authorized_retry_pass": False,
        "allowed_test_failure_selector_digest": None,
        "result_mapping_schema": "qep.case-result-mapping.v1",
        "result_mapping_version": 1,
        "result_mapping_digest": _digest("mapping"),
        "approval_record_digest": _digest("approval"),
    }
    values.update(changes)
    return BatchSuccessPolicy(**values)


def test_success_policy_digest_is_deterministic_and_binds_every_semantic_field() -> None:
    policy = _policy()
    assert policy.policy_digest == _policy().policy_digest
    changes = {
        "policy_id": "policy-002",
        "policy_version": 2,
        "suite_id": "suite-002",
        "max_test_failed_items": 2,
        "allow_authorized_retry_pass": True,
        "allowed_test_failure_selector_digest": _digest("selector"),
        "result_mapping_schema": "qep.case-result-mapping.v2",
        "result_mapping_version": 2,
        "result_mapping_digest": _digest("mapping-2"),
        "approval_record_digest": _digest("approval-2"),
    }
    assert all(
        _policy(**{field: value}).policy_digest != policy.policy_digest
        for field, value in changes.items()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "other"),
        ("policy_id", " "),
        ("suite_id", object()),
        ("policy_version", True),
        ("policy_version", 0),
        ("max_test_failed_items", True),
        ("max_test_failed_items", -1),
        ("allow_authorized_retry_pass", 1),
        ("allowed_test_failure_selector_digest", object()),
        ("result_mapping_schema", ""),
        ("result_mapping_version", 0),
        ("result_mapping_digest", object()),
        ("approval_record_digest", object()),
    ],
)
def test_success_policy_rejects_invalid_contract(field, value) -> None:
    with pytest.raises(DomainValidationError) as caught:
        _policy(**{field: value})
    assert caught.value.field == field


@pytest.mark.parametrize(
    ("field", "value"),
    [("original_denominator", True), ("passed_count", -1), ("unknown_lineage_count", object())],
)
def test_resolution_counts_reject_invalid_numbers(field, value) -> None:
    counts = BatchResolutionCounts(1, 1, 0, 0, 0, 0, 0)
    with pytest.raises(DomainValidationError) as caught:
        replace(counts, **{field: value})
    assert caught.value.field == field


def test_resolution_counts_require_complete_nonzero_denominator() -> None:
    with pytest.raises(DomainValidationError, match="count_mismatch"):
        BatchResolutionCounts(2, 1, 0, 0, 0, 0, 0)
    with pytest.raises(DomainValidationError, match="empty"):
        BatchResolutionCounts(0, 0, 0, 0, 0, 0, 0)


def test_outcome_truth_table_is_exhaustive_for_small_count_space() -> None:
    policy = _policy(max_test_failed_items=1)
    for values in product(range(3), repeat=6):
        if sum(values) == 0:
            continue
        p, t, i, c, u, n = values
        counts = BatchResolutionCounts(sum(values), p, t, i, c, u, n)
        cancel_digest = _digest("cancel") if c + n else None
        decision = evaluate_batch_outcome(
            counts=counts,
            policy=policy,
            suite_id="suite-001",
            batch_cancellation_intent_digest=cancel_digest,
        )
        if c + n == counts.original_denominator and p == t == i == u == 0:
            expected = BatchState.CANCELLED
        elif c + n + u > 0:
            expected = BatchState.PARTIAL
        elif i > 0 or t > policy.max_test_failed_items:
            expected = BatchState.FAILED
        else:
            expected = BatchState.SUCCEEDED
        assert decision == BatchOutcomeEvaluation(
            expected, counts, policy.policy_digest, "suite-001", cancel_digest
        )


def test_cancelled_or_not_executed_without_cancel_authority_fails_closed() -> None:
    counts = BatchResolutionCounts(1, 0, 0, 0, 1, 0, 0)
    with pytest.raises(DomainValidationError, match="cancellation_intent"):
        evaluate_batch_outcome(
            counts=counts,
            policy=_policy(),
            suite_id="suite-001",
            batch_cancellation_intent_digest=None,
        )


@pytest.mark.parametrize(
    "policy",
    [
        _policy(allow_authorized_retry_pass=True),
        _policy(allowed_test_failure_selector_digest=_digest("selector")),
    ],
)
def test_h1_evaluator_fails_closed_until_item_level_policy_facts_exist(policy) -> None:
    counts = BatchResolutionCounts(1, 1, 0, 0, 0, 0, 0)
    with pytest.raises(DomainValidationError, match="unsupported_in_h1"):
        evaluate_batch_outcome(
            counts=counts,
            policy=policy,
            suite_id="suite-001",
            batch_cancellation_intent_digest=None,
        )


def test_evaluator_rejects_untyped_inputs() -> None:
    counts = BatchResolutionCounts(1, 1, 0, 0, 0, 0, 0)
    for change in (
        {"counts": object()},
        {"policy": object()},
        {"suite_id": object()},
        {"batch_cancellation_intent_digest": object()},
    ):
        values = {
            "counts": counts,
            "policy": _policy(),
            "suite_id": "suite-001",
            "batch_cancellation_intent_digest": None,
        }
        values.update(change)
        with pytest.raises(DomainValidationError):
            evaluate_batch_outcome(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", BatchState.REJECTED),
        ("outcome", object()),
        ("counts", object()),
        ("policy_digest", object()),
        ("suite_id", " "),
        ("batch_cancellation_intent_digest", object()),
    ],
)
def test_outcome_evaluation_rejects_invalid_contract(field, value) -> None:
    evaluation = BatchOutcomeEvaluation(
        BatchState.SUCCEEDED,
        BatchResolutionCounts(1, 1, 0, 0, 0, 0, 0),
        _policy().policy_digest,
        "suite-001",
        None,
    )
    with pytest.raises(DomainValidationError) as caught:
        replace(evaluation, **{field: value})
    assert caught.value.field == field


def test_evaluator_rejects_suite_mismatch() -> None:
    with pytest.raises(DomainValidationError, match="suite_id"):
        evaluate_batch_outcome(
            counts=BatchResolutionCounts(1, 1, 0, 0, 0, 0, 0),
            policy=_policy(),
            suite_id="suite-other",
            batch_cancellation_intent_digest=None,
        )


def test_evaluation_digest_is_deterministic_and_binds_all_inputs_and_result() -> None:
    policy = _policy()
    counts = BatchResolutionCounts(2, 1, 1, 0, 0, 0, 0)
    baseline = BatchOutcomeEvaluation(
        BatchState.SUCCEEDED, counts, policy.policy_digest, "suite-001", None
    )
    assert baseline.evaluation_digest == replace(baseline).evaluation_digest
    variants = (
        replace(baseline, outcome=BatchState.FAILED),
        replace(baseline, counts=BatchResolutionCounts(2, 2, 0, 0, 0, 0, 0)),
        replace(baseline, policy_digest=_digest("other-policy")),
        replace(baseline, suite_id="suite-002"),
        replace(baseline, batch_cancellation_intent_digest=_digest("cancel-1")),
        replace(baseline, batch_cancellation_intent_digest=_digest("cancel-2")),
    )
    assert all(item.evaluation_digest != baseline.evaluation_digest for item in variants)
    assert variants[-1].evaluation_digest != variants[-2].evaluation_digest
