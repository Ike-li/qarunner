from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    AttemptExecutionFact,
    Digest,
    PlatformRetryPolicy,
    RetryBudget,
    RetryBudgetUsage,
    RetryDecision,
    RetryDecisionSourceKind,
    RetryPolicyAuthority,
    RetryPolicyFamily,
    RetryReasonSelector,
    RetryRequestedBudget,
    RetryScope,
    RetrySourceFact,
    SuiteRetryPolicy,
    evaluate_run_retry,
)
from qarunner.domain.errors import DomainValidationError

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def d(value: str) -> Digest:
    return Digest(f"sha256:{value * 64}")


def budget() -> RetryBudget:
    return RetryBudget(max_retries=1, max_execution_seconds=100, max_resource_unit_seconds=200)


def source(outcome: AttemptExecutionFact = AttemptExecutionFact.TEST_FAILED) -> RetrySourceFact:
    return RetrySourceFact(
        run_id="run-1",
        run_version=0,
        attempt_id="attempt-1",
        attempt_no=1,
        attempt_fence=7,
        outcome=outcome,
        reason_class="assertion",
        reason_code="flaky",
        item_resolution_set_digest=d("1"),
        source_item_set_digest=d("2"),
        target_item_set_digest=d("2"),
        prior_execution_stop_digest=None,
    )


def authority(family: RetryPolicyFamily = RetryPolicyFamily.SUITE) -> RetryPolicyAuthority:
    return RetryPolicyAuthority(
        schema="qep.retry-policy-authority.v1",
        authority_id="auth-1",
        version=1,
        family=family,
        project_id="project-1",
        suite_revision_id="suite-1" if family is RetryPolicyFamily.SUITE else None,
        runner_id="runner-1" if family is RetryPolicyFamily.PLATFORM else None,
        runner_version=1 if family is RetryPolicyFamily.PLATFORM else None,
        approver_role="suite_owner" if family is RetryPolicyFamily.SUITE else "platform_operator",
        approval_record_digest=d("3"),
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=1),
        authority_digest=d("4"),
    )


def suite_policy() -> SuiteRetryPolicy:
    return SuiteRetryPolicy(
        policy_id="suite-policy",
        version=1,
        project_id="project-1",
        suite_revision_id="suite-1",
        retryable_reasons=(RetryReasonSelector("assertion", "flaky"),),
        required_approver_role="suite_owner",
        max_retries=1,
        retry_scope=RetryScope.FULL_RUN,
        budget=budget(),
        effective_from=NOW - timedelta(days=1),
        effective_until=NOW + timedelta(days=1),
        policy_digest=d("5"),
    )


def platform_policy() -> PlatformRetryPolicy:
    return PlatformRetryPolicy(
        policy_id="platform-policy",
        version=2,
        project_id="project-1",
        runner_id="runner-1",
        runner_version=1,
        retryable_reasons=(RetryReasonSelector("platform", "worker_lost"),),
        required_approver_role="platform_operator",
        max_retries=2,
        retry_scope=RetryScope.FULL_RUN,
        budget=RetryBudget(2, 100, 200),
        require_prior_execution_stop=True,
        effective_from=NOW - timedelta(days=1),
        effective_until=NOW + timedelta(days=1),
        policy_digest=d("6"),
    )


def evaluate(policy=None, fact=None, auth=None, usage=None, requested=None):
    return evaluate_run_retry(
        policy=policy or suite_policy(),
        source=fact or source(),
        authority=auth or authority(),
        usage=usage or RetryBudgetUsage(0, 10, 10),
        requested=requested or RetryRequestedBudget(20, 20),
        retry_intent_digest=d("7"),
        decided_at=NOW,
    )


def test_suite_retry_is_immutable_canonical_decision():
    result = evaluate()
    assert result.decision is RetryDecision.RETRY
    assert result.decision_source_kind is RetryDecisionSourceKind.SUITE_POLICY
    assert result.retry_scope is RetryScope.FULL_RUN
    assert result.retry_intent_digest == d("7")
    assert result.decision_digest == result.decision_digest
    assert result.canonical_payload()["attempt_budget_snapshot"] == {
        "configured": {
            "max_retries": 1,
            "max_execution_seconds": 100,
            "max_resource_unit_seconds": 200,
        },
        "requested": {"execution_seconds": 20, "resource_unit_seconds": 20},
        "usage": {"retry_count": 0, "execution_seconds": 10, "resource_unit_seconds": 10},
    }


def test_platform_retry_requires_stop_proof_and_platform_authority():
    fact = replace(
        source(AttemptExecutionFact.INFRA_FAILED),
        reason_class="platform",
        reason_code="worker_lost",
        prior_execution_stop_digest=d("8"),
    )
    result = evaluate(platform_policy(), fact, authority(RetryPolicyFamily.PLATFORM))
    assert result.decision is RetryDecision.RETRY
    assert result.decision_source_kind is RetryDecisionSourceKind.PLATFORM_POLICY
    assert result.prior_execution_stop_digest == d("8")


@pytest.mark.parametrize(
    "outcome",
    [
        AttemptExecutionFact.PASSED,
        AttemptExecutionFact.CANCELLED,
        AttemptExecutionFact.ATTEMPT_UNKNOWN,
    ],
)
def test_non_policy_outcomes_fail_closed(outcome):
    assert evaluate(fact=source(outcome)).decision is RetryDecision.CLOSED_NO_RETRY


@pytest.mark.parametrize(
    "change",
    [
        lambda p: replace(p, retryable_reasons=(RetryReasonSelector("assertion", "other"),)),
        lambda p: replace(p, effective_until=NOW),
        lambda p: replace(p, project_id="other"),
    ],
)
def test_missing_unknown_expired_or_scope_mismatch_fails_closed(change):
    assert evaluate(policy=change(suite_policy())).decision is RetryDecision.CLOSED_NO_RETRY


def test_family_and_approval_mismatch_fail_closed():
    assert (
        evaluate(auth=authority(RetryPolicyFamily.PLATFORM)).decision
        is RetryDecision.CLOSED_NO_RETRY
    )


def test_reason_allowlist_matches_exact_pairs_not_cross_product():
    policy = replace(
        suite_policy(),
        retryable_reasons=(
            RetryReasonSelector("assertion", "flaky"),
            RetryReasonSelector("timeout", "deadline"),
        ),
    )
    crossed = replace(source(), reason_class="assertion", reason_code="deadline")
    assert evaluate(policy=policy, fact=crossed).decision is RetryDecision.CLOSED_NO_RETRY


def test_policy_explicitly_pins_required_approver_role():
    policy = replace(suite_policy(), required_approver_role="suite_reviewer")
    assert evaluate(policy=policy).decision is RetryDecision.CLOSED_NO_RETRY
    matching = replace(authority(), approver_role="suite_reviewer")
    approved = evaluate(policy=policy, auth=matching)
    assert approved.decision is RetryDecision.RETRY
    changed_digest = replace(policy, policy_digest=d("9"))
    assert (
        evaluate(policy=changed_digest, auth=matching).decision_digest != approved.decision_digest
    )
    assert (
        evaluate(auth=replace(authority(), approver_role="platform_operator")).decision
        is RetryDecision.CLOSED_NO_RETRY
    )


@pytest.mark.parametrize(
    "usage,requested",
    [
        (RetryBudgetUsage(1, 0, 0), RetryRequestedBudget(1, 1)),
        (RetryBudgetUsage(0, 90, 0), RetryRequestedBudget(11, 1)),
        (RetryBudgetUsage(0, 0, 190), RetryRequestedBudget(1, 11)),
    ],
)
def test_any_exhausted_budget_fails_closed(usage, requested):
    result = evaluate(usage=usage, requested=requested)
    assert result.decision is RetryDecision.CLOSED_NO_RETRY
    assert result.retry_intent_digest is None


def test_platform_missing_stop_proof_fails_closed():
    fact = replace(
        source(AttemptExecutionFact.INFRA_FAILED),
        reason_class="platform",
        reason_code="worker_lost",
    )
    assert (
        evaluate(platform_policy(), fact, authority(RetryPolicyFamily.PLATFORM)).decision
        is RetryDecision.CLOSED_NO_RETRY
    )


@pytest.mark.parametrize(
    "make",
    [
        lambda: RetryBudget(-1, 1, 1),
        lambda: RetryBudget(1, -1, 1),
        lambda: RetryBudgetUsage(True, 1, 1),
        lambda: RetryRequestedBudget(0, 1),
        lambda: replace(suite_policy(), max_retries=2),
        lambda: replace(platform_policy(), max_retries=3),
    ],
)
def test_values_reject_invalid_limits(make):
    with pytest.raises(DomainValidationError):
        make()


def test_source_requires_full_run_item_identity_and_valid_numbers():
    with pytest.raises(DomainValidationError):
        replace(source(), run_version=True)
    with pytest.raises(DomainValidationError):
        replace(source(), target_item_set_digest=d("9"))


def test_decision_digest_changes_with_semantic_input():
    assert (
        evaluate().decision_digest
        != evaluate(requested=RetryRequestedBudget(21, 20)).decision_digest
    )


def test_missing_policy_and_authority_fail_closed_without_defaults():
    common = {
        "source": source(),
        "usage": RetryBudgetUsage(0, 1, 1),
        "requested": RetryRequestedBudget(1, 1),
        "retry_intent_digest": d("7"),
        "decided_at": NOW,
    }
    assert (
        evaluate_run_retry(policy=None, authority=None, **common).decision
        is RetryDecision.CLOSED_NO_RETRY
    )
    assert (
        evaluate_run_retry(policy=suite_policy(), authority=None, **common).decision
        is RetryDecision.CLOSED_NO_RETRY
    )


@pytest.mark.parametrize(
    "make",
    [
        lambda: replace(platform_policy(), require_prior_execution_stop=False),
        lambda: replace(source(), outcome="test_failed"),
        lambda: replace(authority(), family="suite"),
        lambda: replace(authority(), runner_id="runner"),
        lambda: replace(authority(RetryPolicyFamily.PLATFORM), suite_revision_id="suite"),
        lambda: replace(suite_policy(), retry_scope="full_run"),
        lambda: replace(suite_policy(), retryable_reasons=("invalid",)),
        lambda: replace(
            suite_policy(),
            retryable_reasons=(
                RetryReasonSelector("z", "z"),
                RetryReasonSelector("a", "a"),
            ),
        ),
        lambda: replace(suite_policy(), effective_until=NOW - timedelta(days=2)),
        lambda: replace(suite_policy(), effective_from=NOW.replace(tzinfo=None)),
        lambda: replace(suite_policy(), policy_digest="bad"),
        lambda: replace(suite_policy(), policy_id=""),
    ],
)
def test_contract_values_reject_malformed_authority_policy_and_source(make):
    with pytest.raises(DomainValidationError):
        make()


def test_decision_enforces_retry_intent_presence_matrix():
    with pytest.raises(DomainValidationError):
        replace(evaluate(), retry_intent_digest=None)
    closed = evaluate(usage=RetryBudgetUsage(1, 0, 0))
    with pytest.raises(DomainValidationError):
        replace(closed, retry_intent_digest=d("7"))


@pytest.mark.parametrize(
    "change",
    [
        {"schema": ""},
        {"version": 0},
        {"approval_record_digest": "bad"},
        {"valid_until": NOW - timedelta(hours=2)},
    ],
)
def test_authority_rejects_incomplete_approval(change):
    with pytest.raises(DomainValidationError):
        replace(authority(), **change)


def test_evaluator_rejects_invalid_time_and_candidate_digest():
    kwargs = {
        "policy": suite_policy(),
        "source": source(),
        "authority": authority(),
        "usage": RetryBudgetUsage(0, 1, 1),
        "requested": RetryRequestedBudget(1, 1),
        "retry_intent_digest": d("7"),
        "decided_at": NOW,
    }
    with pytest.raises(DomainValidationError):
        evaluate_run_retry(**(kwargs | {"decided_at": NOW.replace(tzinfo=None)}))
    with pytest.raises(DomainValidationError):
        evaluate_run_retry(**(kwargs | {"retry_intent_digest": "bad"}))
