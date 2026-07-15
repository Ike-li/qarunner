from dataclasses import replace
from datetime import UTC, datetime

import pytest

from qarunner.domain import (
    AttemptAuthority,
    AttemptExecutionFact,
    CancellationSource,
    Digest,
    PlatformExitClass,
    PolicyRetryAuthority,
    RetryDecision,
    RetryDecisionSourceKind,
    RetryIntent,
    RetryPolicyFamily,
    RetryProvenance,
    Run,
    RunState,
    UnknownAdjudicationDecision,
    build_evidence_manifest,
    evaluate_run_retry,
)
from qarunner.domain.errors import (
    DomainValidationError,
    IdempotencyConflict,
    RetryNotAllowed,
    VersionConflict,
)


def d(value: str) -> Digest:
    return Digest(f"sha256:{value * 64}")


def policy_authority() -> PolicyRetryAuthority:
    return PolicyRetryAuthority(
        family=RetryPolicyFamily.SUITE,
        decision_source_kind=RetryDecisionSourceKind.SUITE_POLICY,
        policy_digest=d("1"),
        authority_schema="qep.retry-policy-authority.v1",
        authority_id="authority-1",
        authority_version=1,
        authority_digest=d("2"),
        source_outcome=AttemptExecutionFact.TEST_FAILED,
        item_resolution_set_digest=d("3"),
        source_item_set_digest=d("4"),
        target_item_set_digest=d("4"),
    )


def test_policy_retry_intent_uses_v2_typed_authority_without_decision_digest_cycle() -> None:
    intent = RetryIntent(
        id="retry-1",
        run_id="run-1",
        source_attempt_id="attempt-1",
        source_attempt_no=1,
        source_fence=1,
        authority=policy_authority(),
        execution_spec_digest=d("5"),
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert intent.digest == intent.digest
    assert intent.authority.decision_source_kind is RetryDecisionSourceKind.SUITE_POLICY
    assert not hasattr(intent.authority, "run_retry_decision_digest")


def test_policy_authority_rejects_family_outcome_and_full_set_mismatches() -> None:
    with pytest.raises(DomainValidationError, match="family.*unknown"):
        replace(policy_authority(), family="suite")
    with pytest.raises(DomainValidationError, match="source_outcome.*family_mismatch"):
        replace(policy_authority(), source_outcome=AttemptExecutionFact.INFRA_FAILED)
    with pytest.raises(DomainValidationError, match="decision_source_kind.*family_mismatch"):
        replace(
            policy_authority(),
            decision_source_kind=RetryDecisionSourceKind.PLATFORM_POLICY,
        )
    with pytest.raises(
        DomainValidationError, match="target_item_set_digest.*full_run_scope_mismatch"
    ):
        replace(policy_authority(), target_item_set_digest=d("9"))
    with pytest.raises(DomainValidationError, match="authority_version.*not_integer"):
        replace(policy_authority(), authority_version=True)
    with pytest.raises(DomainValidationError, match="policy_digest.*not_digest"):
        replace(policy_authority(), policy_digest="bad")


def test_platform_policy_authority_and_policy_provenance_are_canonical() -> None:
    authority = replace(
        policy_authority(),
        family=RetryPolicyFamily.PLATFORM,
        decision_source_kind=RetryDecisionSourceKind.PLATFORM_POLICY,
        source_outcome=AttemptExecutionFact.INFRA_FAILED,
    )
    intent = RetryIntent(
        id="retry-1",
        run_id="run-1",
        source_attempt_id="attempt-1",
        source_attempt_no=1,
        source_fence=1,
        authority=authority,
        execution_spec_digest=d("5"),
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    provenance = RetryProvenance.from_intent(intent)

    assert provenance.digest == provenance.digest
    assert intent.adjudication_id == ""
    assert intent.adjudication_digest is None
    assert intent.decision is None
    assert provenance.adjudication_id == ""
    assert provenance.adjudication_digest is None
    assert provenance.decision is None


@pytest.mark.parametrize("value", [object(), None])
def test_intent_and_provenance_reject_untyped_authority(value: object) -> None:
    with pytest.raises(DomainValidationError, match="authority.*unknown"):
        RetryIntent(
            id="retry-1",
            run_id="run-1",
            source_attempt_id="attempt-1",
            source_attempt_no=1,
            source_fence=1,
            authority=value,  # type: ignore[arg-type]
            execution_spec_digest=d("5"),
            created_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
    with pytest.raises(DomainValidationError, match="authority.*unknown"):
        RetryProvenance("retry-1", d("6"), "attempt-1", 1, 1, value)  # type: ignore[arg-type]


def test_run_exposes_one_policy_queue_seam() -> None:
    assert callable(Run.queue_policy_retry)


def terminal_run(outcome: AttemptExecutionFact):
    from tests.unit.domain.test_cancellation import (
        _cancel_requested_uploading_run,
        _passing_finalize_inputs,
    )

    requested, worker = _cancel_requested_uploading_run()
    running = replace(requested, cancel_intent=None)
    attempt = running.attempts[-1]
    proposal, trusted_exit, summary, artifacts, requirements = _passing_finalize_inputs(
        run=running
    )
    if outcome is AttemptExecutionFact.TEST_FAILED:
        summary = replace(summary, passed=0, failed=1)
        trusted_exit = replace(trusted_exit, exit_code=1)
    else:
        trusted_exit = replace(
            trusted_exit,
            exit_class=PlatformExitClass.INFRA_FAILED,
            exit_code=2,
        )
        summary = None
        artifacts = ()
        requirements = replace(requirements, required_artifact_paths=frozenset())
    candidate = build_evidence_manifest(
        attempt_id=attempt.id,
        run_id=attempt.run_id,
        attempt_no=attempt.attempt_no,
        assignment_id=attempt.assignment_id,
        fence=attempt.fence,
        worker=attempt.worker,
        execution_spec_digest=attempt.spec_digest,
        trusted_exit=trusted_exit,
        case_summary=summary,
        artifacts=artifacts,
    )
    finalized = running.finalize_current_attempt_evidence(
        attempt_id=attempt.id,
        proposal=replace(proposal, root_digest=candidate.root_digest),
        trusted_exit=trusted_exit,
        case_summary=summary,
        artifacts=artifacts,
        requirements=requirements,
        authority=AttemptAuthority(attempt.fence, worker.ref),
        worker=worker.ref,
        fence=attempt.fence,
        expected_version=running.version,
        expected_attempt_version=attempt.version,
    )
    return finalized.run, worker


def policy_case(outcome: AttemptExecutionFact):
    from qarunner.domain import RetryBudgetUsage, RetryRequestedBudget, RetrySourceFact
    from tests.unit.domain.test_run_retry_policy import (
        authority as policy_approval,
    )
    from tests.unit.domain.test_run_retry_policy import (
        platform_policy,
        suite_policy,
    )

    run, worker = terminal_run(outcome)
    family = (
        RetryPolicyFamily.SUITE
        if outcome is AttemptExecutionFact.TEST_FAILED
        else RetryPolicyFamily.PLATFORM
    )
    policy = suite_policy() if family is RetryPolicyFamily.SUITE else platform_policy()
    approval = policy_approval(family)
    source = RetrySourceFact(
        run_id=run.id,
        run_version=run.version,
        attempt_id=run.attempts[-1].id,
        attempt_no=run.attempts[-1].attempt_no,
        attempt_fence=run.attempts[-1].fence,
        outcome=outcome,
        reason_class="assertion" if family is RetryPolicyFamily.SUITE else "platform",
        reason_code="flaky" if family is RetryPolicyFamily.SUITE else "worker_lost",
        item_resolution_set_digest=d("3"),
        source_item_set_digest=d("4"),
        target_item_set_digest=d("4"),
        prior_execution_stop_digest=None if family is RetryPolicyFamily.SUITE else d("8"),
    )
    typed = PolicyRetryAuthority(
        family=family,
        decision_source_kind=(
            RetryDecisionSourceKind.SUITE_POLICY
            if family is RetryPolicyFamily.SUITE
            else RetryDecisionSourceKind.PLATFORM_POLICY
        ),
        policy_digest=policy.policy_digest,
        authority_schema=approval.schema,
        authority_id=approval.authority_id,
        authority_version=approval.version,
        authority_digest=approval.authority_digest,
        source_outcome=outcome,
        item_resolution_set_digest=source.item_resolution_set_digest,
        source_item_set_digest=source.source_item_set_digest,
        target_item_set_digest=source.target_item_set_digest,
    )
    intent = RetryIntent(
        id="policy-retry-1",
        run_id=run.id,
        source_attempt_id=source.attempt_id,
        source_attempt_no=source.attempt_no,
        source_fence=source.attempt_fence,
        authority=typed,
        execution_spec_digest=run.attempts[-1].spec_digest,
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    decision = evaluate_run_retry(
        policy=policy,
        source=source,
        authority=approval,
        usage=RetryBudgetUsage(0, 10, 10),
        requested=RetryRequestedBudget(20, 20),
        retry_intent_digest=intent.digest,
        decided_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    return run, worker, intent, decision


@pytest.mark.parametrize(
    "outcome", [AttemptExecutionFact.TEST_FAILED, AttemptExecutionFact.INFRA_FAILED]
)
def test_policy_retry_queues_in_shared_chain_and_replays(outcome: AttemptExecutionFact) -> None:
    run, _, intent, decision = policy_case(outcome)
    queued = run.queue_policy_retry(
        retry_intent=intent, retry_decision=decision, expected_version=run.version
    )

    assert queued.state is RunState.RETRY_QUEUED
    assert queued.pending_retry_intent is intent
    assert (
        queued.queue_policy_retry(
            retry_intent=intent, retry_decision=decision, expected_version=run.version
        )
        is queued
    )


@pytest.mark.parametrize(
    "case",
    [
        "closed",
        "intent_digest",
        "run_version",
        "attempt_id",
        "attempt_no",
        "attempt_fence",
        "outcome",
        "resolution",
        "source_set",
        "target_set",
        "decision_kind",
        "policy",
        "authority_schema",
        "authority_id",
        "authority_version",
        "authority_digest",
        "spec",
    ],
)
def test_policy_retry_rejects_every_decision_and_source_continuity_mismatch(case: str) -> None:
    run, _, intent, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    source = decision.source
    authority = intent.authority
    if case == "closed":
        decision = replace(
            decision, decision=RetryDecision.CLOSED_NO_RETRY, retry_intent_digest=None
        )
    elif case == "intent_digest":
        decision = replace(decision, retry_intent_digest=d("9"))
    elif case == "run_version":
        decision = replace(decision, source=replace(source, run_version=source.run_version + 1))
    elif case == "attempt_id":
        decision = replace(decision, source=replace(source, attempt_id="other"))
    elif case == "attempt_no":
        decision = replace(decision, source=replace(source, attempt_no=2))
    elif case == "attempt_fence":
        decision = replace(decision, source=replace(source, attempt_fence=2))
    elif case == "outcome":
        decision = replace(
            decision, source=replace(source, outcome=AttemptExecutionFact.INFRA_FAILED)
        )
    elif case == "resolution":
        decision = replace(decision, source=replace(source, item_resolution_set_digest=d("9")))
    elif case == "source_set":
        authority = replace(
            authority, source_item_set_digest=d("9"), target_item_set_digest=d("9")
        )
        intent = replace(intent, authority=authority)
    elif case == "target_set":
        decision = replace(
            decision,
            source=replace(source, source_item_set_digest=d("9"), target_item_set_digest=d("9")),
        )
    elif case == "decision_kind":
        decision = replace(decision, decision_source_kind=RetryDecisionSourceKind.PLATFORM_POLICY)
    elif case == "policy":
        decision = replace(decision, policy_digest=d("9"))
    elif case == "authority_schema":
        decision = replace(decision, authority_schema="other")
    elif case == "authority_id":
        decision = replace(decision, authority_id="other")
    elif case == "authority_version":
        decision = replace(decision, authority_version=2)
    elif case == "authority_digest":
        decision = replace(decision, authority_digest=d("9"))
    else:
        intent = replace(intent, execution_spec_digest=d("9"))

    with pytest.raises(RetryNotAllowed, match="retry_intent_mismatch"):
        run.queue_policy_retry(
            retry_intent=intent, retry_decision=decision, expected_version=run.version
        )


def test_policy_retry_rejects_unknown_authority_stale_version_and_poison_replay() -> None:
    run, _, intent, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    unknown = RetryIntent.from_unknown_adjudication(
        id="unknown",
        run_id=run.id,
        source_attempt_id=intent.source_attempt_id,
        source_attempt_no=1,
        source_fence=1,
        adjudication_id="adj",
        adjudication_digest=d("8"),
        decision=UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY,
        execution_spec_digest=intent.execution_spec_digest,
        created_at=intent.created_at,
    )
    with pytest.raises(RetryNotAllowed, match="policy_authority_required"):
        run.queue_policy_retry(
            retry_intent=unknown, retry_decision=decision, expected_version=run.version
        )
    with pytest.raises(VersionConflict):
        run.queue_policy_retry(retry_intent=intent, retry_decision=decision, expected_version=0)
    queued = run.queue_policy_retry(
        retry_intent=intent, retry_decision=decision, expected_version=run.version
    )
    poisoned = replace(intent, created_at=intent.created_at.replace(hour=1))
    with pytest.raises(IdempotencyConflict):
        queued.queue_policy_retry(
            retry_intent=poisoned, retry_decision=decision, expected_version=queued.version
        )


def test_cancel_and_existing_pending_retry_win_policy_queue_races() -> None:
    run, _, intent, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    cancelled = run.request_cancel(
        cancel_key="cancel-policy",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-1",
        reason="stop",
        observed_at=datetime(2026, 7, 15, tzinfo=UTC),
        expected_version=run.version,
    )
    with pytest.raises(RetryNotAllowed, match="cancel_requested"):
        cancelled.queue_policy_retry(
            retry_intent=intent, retry_decision=decision, expected_version=cancelled.version
        )
    queued = run.queue_policy_retry(
        retry_intent=intent, retry_decision=decision, expected_version=run.version
    )
    second = replace(intent, id="policy-retry-2")
    second_decision = replace(decision, retry_intent_digest=second.digest)
    with pytest.raises(RetryNotAllowed, match="retry_already_queued"):
        queued.queue_policy_retry(
            retry_intent=second, retry_decision=second_decision, expected_version=queued.version
        )


def test_unknown_source_cannot_enter_automatic_policy_retry() -> None:
    from tests.unit.domain.test_unknown_adjudicated_retry import _unknown_run

    unknown, _, _ = _unknown_run()
    _, _, template, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    source = replace(
        decision.source,
        run_id=unknown.id,
        run_version=unknown.version,
        attempt_id=unknown.attempts[-1].id,
        attempt_no=unknown.attempts[-1].attempt_no,
        attempt_fence=unknown.attempts[-1].fence,
    )
    intent = replace(
        template,
        run_id=unknown.id,
        source_attempt_id=source.attempt_id,
        source_attempt_no=source.attempt_no,
        source_fence=source.attempt_fence,
        execution_spec_digest=unknown.attempts[-1].spec_digest,
    )
    decision = replace(decision, source=source, retry_intent_digest=intent.digest)
    with pytest.raises(RetryNotAllowed, match="retry_intent_mismatch"):
        unknown.queue_policy_retry(
            retry_intent=intent, retry_decision=decision, expected_version=unknown.version
        )


def test_policy_provenance_is_copied_at_commit_start_and_rehydrates() -> None:
    from tests.unit.domain.test_unknown_adjudicated_retry import _ready_worker

    run, _, intent, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    queued = run.queue_policy_retry(
        retry_intent=intent, retry_decision=decision, expected_version=run.version
    )
    worker, worker_authority = _ready_worker(generation=4)
    offered = queued.offer_assignment(
        assignment_id="assignment-policy-2",
        worker=worker,
        worker_authority=worker_authority,
        spec_digest=intent.execution_spec_digest,
        offered_at=datetime(2026, 7, 15, 1, tzinfo=UTC),
        expires_at=datetime(2026, 7, 15, 2, tzinfo=UTC),
        expected_version=queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-policy-2",
        worker=worker.ref,
        observed_at=datetime(2026, 7, 15, 1, 1, tzinfo=UTC),
        expected_version=offered.version,
    )
    committed = claimed.commit_start(
        assignment_id="assignment-policy-2",
        worker=worker.ref,
        start_commit_key="commit-policy-2",
        spec_digest=intent.execution_spec_digest,
        new_attempt_id="attempt-policy-2",
        observed_at=datetime(2026, 7, 15, 1, 2, tzinfo=UTC),
        expected_version=claimed.version,
    )

    assert committed.attempt.retry_provenance.authority == intent.authority
    assert replace(committed.run) == committed.run


def test_run_rehydration_rejects_duplicate_policy_authority() -> None:
    run, _, intent, decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    queued = run.queue_policy_retry(
        retry_intent=intent, retry_decision=decision, expected_version=run.version
    )
    with pytest.raises(DomainValidationError, match="retry_intents.*duplicate_authority"):
        replace(
            queued,
            retry_intents=(intent, replace(intent, id="duplicate")),
            pending_retry_intent_id="duplicate",
        )


def test_run_rehydration_rejects_policy_authority_for_wrong_source_state() -> None:
    suite_run, _, suite_intent, suite_decision = policy_case(AttemptExecutionFact.TEST_FAILED)
    queued = suite_run.queue_policy_retry(
        retry_intent=suite_intent,
        retry_decision=suite_decision,
        expected_version=suite_run.version,
    )
    _, _, platform_intent, _ = policy_case(AttemptExecutionFact.INFRA_FAILED)
    mismatched = replace(
        suite_intent,
        authority=platform_intent.authority,
    )
    with pytest.raises(DomainValidationError, match="retry_intents.*policy_not_authoritative"):
        replace(queued, retry_intents=(mismatched,))


def test_commit_start_validator_rejects_tampered_stored_policy_source_link() -> None:
    run, _, intent, _ = policy_case(AttemptExecutionFact.TEST_FAILED)
    with pytest.raises(RetryNotAllowed, match="retry_intent_mismatch"):
        run._ensure_retry_intent_authoritative(
            retry_intent=replace(intent, source_attempt_no=2),
            source=run.attempts[-1],
        )
