"""T-M0-UNKNOWN-001C: adjudication gates a fresh Assignment and Attempt."""

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta

import pytest

INITIAL_OFFERED_AT = datetime(2026, 7, 12, 18, tzinfo=UTC)
INITIAL_CLAIMED_AT = INITIAL_OFFERED_AT + timedelta(minutes=1)
INITIAL_COMMITTED_AT = INITIAL_CLAIMED_AT + timedelta(minutes=1)
INITIAL_EXPIRES_AT = INITIAL_OFFERED_AT + timedelta(hours=1)
RETRY_OFFERED_AT = datetime(2026, 7, 12, 22, 1, tzinfo=UTC)
RETRY_CLAIMED_AT = RETRY_OFFERED_AT + timedelta(minutes=1)
RETRY_COMMITTED_AT = RETRY_CLAIMED_AT + timedelta(minutes=1)
RETRY_EXPIRES_AT = RETRY_OFFERED_AT + timedelta(hours=1)


def _unsafe_run_replace(run, **changes):
    """Simulate a corrupt store object that bypassed dataclass rehydration guards."""
    unsafe = object.__new__(type(run))
    for field in fields(run):
        object.__setattr__(
            unsafe,
            field.name,
            changes.get(field.name, getattr(run, field.name)),
        )
    return unsafe


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-retry-fact.v1",
        payload={"label": label},
    )


def _ready_worker(*, generation: int):
    from qarunner.domain import (
        WorkerAuthority,
        WorkerGeneration,
        WorkerRef,
        WorkerState,
    )

    registered_at = datetime(2026, 7, 12, 12, tzinfo=UTC) + timedelta(hours=generation)
    worker = WorkerGeneration.register(
        ref=WorkerRef(worker_id="worker-001", generation=generation),
        host_id="host-001",
        pool_id="pool-default",
        cert_serial=f"cert-{generation}",
        agent_version="1.0.0",
        capabilities_digest=_digest(f"capabilities-{generation}"),
        registered_at=registered_at,
    )
    authority = WorkerAuthority(current_ref=worker.ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=authority,
        expected_version=0,
        occurred_at=registered_at + timedelta(seconds=1),
    )
    return ready, authority


def _observation():
    from qarunner.domain import UnknownObservation, UnknownReason, UnknownSource

    return UnknownObservation(
        id="unknown-001",
        reason=UnknownReason.EXECUTION_STOP_UNPROVEN,
        source=UnknownSource.RECONCILER,
        review_basis_digest=_digest("review-basis"),
        recorded_at=datetime(2026, 7, 12, 20, tzinfo=UTC),
    )


def _adjudication(
    *,
    adjudication_id: str = "adjudication-001",
    decision_name: str = "CONFIRM_STOPPED_THEN_RETRY",
    supersedes_adjudication_id: str | None = None,
    occurred_at: datetime = datetime(2026, 7, 12, 21, tzinfo=UTC),
):
    from qarunner.domain import UnknownAdjudication, UnknownAdjudicationDecision

    decision = UnknownAdjudicationDecision[decision_name]
    proof_digest = None
    risk_approver_id = None
    risk_acceptance_digest = None
    if decision is UnknownAdjudicationDecision.CONFIRM_STOPPED_THEN_RETRY:
        proof_digest = _digest("stopped-proof")
    elif decision is UnknownAdjudicationDecision.ACCEPT_DUPLICATE_RISK_THEN_RETRY:
        risk_approver_id = "business-owner-001"
        risk_acceptance_digest = _digest("risk-acceptance")
    return UnknownAdjudication(
        id=adjudication_id,
        attempt_id="attempt-001",
        unknown_observation_digest=_observation().digest,
        decision=decision,
        actor_id="admin-001",
        reason="reviewed execution side effects",
        occurred_at=occurred_at,
        proof_digest=proof_digest,
        risk_approver_id=risk_approver_id,
        risk_acceptance_digest=risk_acceptance_digest,
        evidence_root_digest=None,
        supersedes_adjudication_id=supersedes_adjudication_id,
    )


def _committed_run():
    from qarunner.domain import Run, RunState

    worker, authority = _ready_worker(generation=3)
    spec_digest = _digest("execution-spec")
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=worker,
        worker_authority=authority,
        spec_digest=spec_digest,
        offered_at=INITIAL_OFFERED_AT,
        expires_at=INITIAL_EXPIRES_AT,
        expected_version=queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        observed_at=INITIAL_CLAIMED_AT,
        expected_version=offered.version,
    )
    committed = claimed.commit_start(
        assignment_id="assignment-001",
        worker=worker.ref,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="attempt-001",
        observed_at=INITIAL_COMMITTED_AT,
        expected_version=claimed.version,
    )
    return committed.run, worker, spec_digest


def _unknown_run():
    committed, worker, spec_digest = _committed_run()
    unknown = committed.mark_current_attempt_unknown(
        attempt_id=committed.attempts[-1].id,
        observation=_observation(),
        expected_version=committed.version,
        expected_attempt_version=committed.attempts[-1].version,
    )
    return unknown, worker, spec_digest


def _adjudicated_run(*, decision_name: str = "CONFIRM_STOPPED_THEN_RETRY"):
    unknown, worker, spec_digest = _unknown_run()
    adjudication = _adjudication(decision_name=decision_name)
    adjudicated = unknown.append_current_unknown_adjudication(
        attempt_id="attempt-001",
        adjudication=adjudication,
        expected_version=unknown.version,
        expected_attempt_version=unknown.attempts[-1].version,
    )
    return adjudicated, worker, spec_digest, adjudication


def _retry_intent(
    *,
    adjudication=None,
    adjudication_id: str | None = None,
    intent_id: str = "retry-001",
):
    from qarunner.domain import RetryIntent

    record = adjudication or _adjudication()
    return RetryIntent(
        id=intent_id,
        run_id="run-001",
        source_attempt_id="attempt-001",
        source_attempt_no=1,
        source_fence=1,
        adjudication_id=adjudication_id or record.id,
        adjudication_digest=record.digest,
        decision=record.decision,
        execution_spec_digest=_digest("execution-spec"),
        created_at=datetime(2026, 7, 12, 22, tzinfo=UTC),
    )


def _queued_retry():
    adjudicated, worker, spec_digest, adjudication = _adjudicated_run()
    intent = _retry_intent(adjudication=adjudication)
    retry_queued = adjudicated.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=adjudicated.version,
    )
    return retry_queued, worker, spec_digest, intent


def _committed_retry():
    retry_queued, _, spec_digest, intent = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    offered = retry_queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=retry_queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-002",
        worker=worker2.ref,
        observed_at=RETRY_CLAIMED_AT,
        expected_version=offered.version,
    )
    committed = claimed.commit_start(
        assignment_id="assignment-002",
        worker=worker2.ref,
        start_commit_key="commit-002",
        spec_digest=spec_digest,
        new_attempt_id="attempt-002",
        observed_at=RETRY_COMMITTED_AT,
        expected_version=claimed.version,
    )
    return committed, worker2, spec_digest, intent


def _adjudicated_attempt2_with_retry_intent():
    from qarunner.domain import (
        RetryIntent,
        UnknownAdjudication,
        UnknownObservation,
        UnknownReason,
        UnknownSource,
    )

    run = _committed_retry()[0].run
    attempt2 = run.attempts[-1]
    observation = UnknownObservation(
        id="unknown-002",
        reason=UnknownReason.EXECUTION_STOP_UNPROVEN,
        source=UnknownSource.RECONCILER,
        review_basis_digest=_digest("review-basis-002"),
        recorded_at=datetime(2026, 7, 12, 23, tzinfo=UTC),
    )
    unknown = run.mark_current_attempt_unknown(
        attempt_id=attempt2.id,
        observation=observation,
        expected_version=run.version,
        expected_attempt_version=attempt2.version,
    )
    adjudication = UnknownAdjudication(
        id="adjudication-002",
        attempt_id=attempt2.id,
        unknown_observation_digest=observation.digest,
        decision=_adjudication().decision,
        actor_id="admin-002",
        reason="second execution is externally proven stopped",
        occurred_at=datetime(2026, 7, 13, 0, tzinfo=UTC),
        proof_digest=_digest("stopped-proof-002"),
        risk_approver_id=None,
        risk_acceptance_digest=None,
        evidence_root_digest=None,
    )
    adjudicated = unknown.append_current_unknown_adjudication(
        attempt_id=attempt2.id,
        adjudication=adjudication,
        expected_version=unknown.version,
        expected_attempt_version=unknown.attempts[-1].version,
    )
    intent = RetryIntent(
        id="retry-002",
        run_id=run.id,
        source_attempt_id=attempt2.id,
        source_attempt_no=attempt2.attempt_no,
        source_fence=attempt2.fence,
        adjudication_id=adjudication.id,
        adjudication_digest=adjudication.digest,
        decision=adjudication.decision,
        execution_spec_digest=attempt2.spec_digest,
        created_at=datetime(2026, 7, 13, 1, tzinfo=UTC),
    )
    return adjudicated, intent


def test_retry_intent_preserves_unknown_execution_history_without_allocating_fence() -> None:
    from qarunner.domain import AssignmentState, RunState

    adjudicated, _, _, adjudication = _adjudicated_run()
    attempt1 = adjudicated.attempts[-1]
    assignment1 = adjudicated.assignment
    intent = _retry_intent(adjudication=adjudication)

    retry_queued = adjudicated.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=adjudicated.version,
    )

    assert retry_queued.state is RunState.RETRY_QUEUED
    assert retry_queued.version == adjudicated.version + 1
    assert retry_queued.current_fence == adjudicated.current_fence == 1
    assert retry_queued.attempts == (attempt1,)
    assert retry_queued.attempts[0] is attempt1
    assert retry_queued.assignment is None
    assert retry_queued.assignments == (assignment1,)
    assert retry_queued.assignments[0].state is AssignmentState.COMMITTED
    assert retry_queued.retry_intents == (intent,)
    assert retry_queued.pending_retry_intent == intent
    assert adjudicated.assignment == assignment1
    assert adjudicated.retry_intents == ()


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        pytest.param("missing", "adjudication_missing", id="missing"),
        pytest.param("superseded", "adjudication_not_current", id="superseded"),
        pytest.param("no-retry", "decision_does_not_permit_retry", id="no-retry"),
    ],
)
def test_only_current_retry_permitting_adjudication_can_queue_retry(
    case: str, reason: str
) -> None:
    from qarunner.domain import RetryNotAllowed

    if case == "missing":
        source, _, _ = _unknown_run()
        intent = _retry_intent(adjudication_id="missing")
    elif case == "no-retry":
        source, _, _, adjudication = _adjudicated_run(decision_name="MARK_INFRA_FAILED_NO_RETRY")
        intent = _retry_intent(adjudication=adjudication)
    else:
        first, _, _, old = _adjudicated_run()
        replacement = _adjudication(
            adjudication_id="adjudication-002",
            decision_name="MARK_INFRA_FAILED_NO_RETRY",
            supersedes_adjudication_id=old.id,
            occurred_at=datetime(2026, 7, 12, 21, 30, tzinfo=UTC),
        )
        source = first.append_current_unknown_adjudication(
            attempt_id="attempt-001",
            adjudication=replacement,
            expected_version=first.version,
            expected_attempt_version=first.attempts[-1].version,
        )
        intent = _retry_intent(adjudication=old)
    before = source

    with pytest.raises(RetryNotAllowed) as caught:
        source.queue_adjudicated_retry(
            retry_intent=intent,
            expected_version=source.version,
        )

    assert caught.value.code == "retry_not_allowed"
    assert caught.value.reason == reason
    assert source == before


def test_retry_intent_exact_replay_wins_before_run_cas() -> None:
    adjudicated, _, _, adjudication = _adjudicated_run()
    intent = _retry_intent(adjudication=adjudication)
    first = adjudicated.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=adjudicated.version,
    )

    replay = first.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=adjudicated.version,
    )

    assert replay is first
    assert replay.retry_intents == (intent,)


def test_retry_intent_id_reuse_with_changed_content_conflicts_before_cas() -> None:
    from qarunner.domain import IdempotencyConflict

    adjudicated, _, _, adjudication = _adjudicated_run()
    intent = _retry_intent(adjudication=adjudication)
    first = adjudicated.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=adjudicated.version,
    )
    changed = replace(intent, created_at=intent.created_at + timedelta(seconds=1))

    with pytest.raises(IdempotencyConflict) as caught:
        first.queue_adjudicated_retry(
            retry_intent=changed,
            expected_version=adjudicated.version,
        )

    assert caught.value.scope == "run:run-001:retry-intent"
    assert caught.value.key == intent.id
    assert first.retry_intents == (intent,)


def test_retry_intent_replay_and_conflict_remain_stable_after_attempt2() -> None:
    from qarunner.domain import IdempotencyConflict

    committed, _, _, intent = _committed_retry()

    replay = committed.run.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=0,
    )
    assert replay is committed.run
    assert len(replay.attempts) == 2
    assert replay.current_fence == 2

    with pytest.raises(IdempotencyConflict):
        committed.run.queue_adjudicated_retry(
            retry_intent=replace(
                intent,
                created_at=intent.created_at + timedelta(seconds=1),
            ),
            expected_version=0,
        )
    assert committed.run.retry_intents == (intent,)


def test_retry_intent_first_write_obeys_run_cas() -> None:
    from qarunner.domain import VersionConflict

    adjudicated, _, _, adjudication = _adjudicated_run()

    with pytest.raises(VersionConflict):
        adjudicated.queue_adjudicated_retry(
            retry_intent=_retry_intent(adjudication=adjudication),
            expected_version=adjudicated.version - 1,
        )

    assert adjudicated.retry_intents == ()
    assert adjudicated.pending_retry_intent is None


def test_generic_transition_cannot_bypass_retry_adjudication() -> None:
    from qarunner.domain import InvalidTransition, RunState

    adjudicated, _, _, _ = _adjudicated_run()

    with pytest.raises(InvalidTransition):
        adjudicated.transition(RunState.RETRY_QUEUED, expected_version=adjudicated.version)

    assert adjudicated.state is RunState.RUNNING
    assert adjudicated.retry_intents == ()


def test_pending_retry_intent_blocks_a_new_adjudication_but_allows_exact_replay() -> None:
    from qarunner.domain import RetryNotAllowed

    adjudicated, _, _, first_record = _adjudicated_run()
    retry_queued = adjudicated.queue_adjudicated_retry(
        retry_intent=_retry_intent(adjudication=first_record),
        expected_version=adjudicated.version,
    )
    new_record = _adjudication(
        adjudication_id="adjudication-002",
        decision_name="MARK_INFRA_FAILED_NO_RETRY",
        supersedes_adjudication_id=first_record.id,
        occurred_at=datetime(2026, 7, 12, 22, 30, tzinfo=UTC),
    )

    with pytest.raises(RetryNotAllowed) as caught:
        retry_queued.append_current_unknown_adjudication(
            attempt_id="attempt-001",
            adjudication=new_record,
            expected_version=retry_queued.version,
            expected_attempt_version=retry_queued.attempts[-1].version,
        )

    assert caught.value.reason == "retry_already_queued"
    replay = retry_queued.append_current_unknown_adjudication(
        attempt_id="attempt-001",
        adjudication=first_record,
        expected_version=adjudicated.version,
        expected_attempt_version=0,
    )
    assert replay is retry_queued
    assert retry_queued.attempts[-1].adjudications == (first_record,)


def test_retry_assignment_offer_preserves_history_and_rejects_reused_identity() -> None:
    from qarunner.domain import AssignmentConflict, AssignmentState, RunState

    retry_queued, _, spec_digest, intent = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    assignment1 = retry_queued.assignments[0]

    offered = retry_queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=retry_queued.version,
    )

    assert offered.state is RunState.ASSIGNED
    assert offered.current_fence == 1
    assert len(offered.attempts) == 1
    assert offered.assignments[0] is assignment1
    assert offered.assignments[1] == offered.assignment
    assert offered.assignment is not None
    assert offered.assignment.id == "assignment-002"
    assert offered.assignment.state is AssignmentState.OFFERED
    assert offered.assignment.retry_intent_id == intent.id
    assert offered.pending_retry_intent == intent

    with pytest.raises(AssignmentConflict) as caught:
        retry_queued.offer_assignment(
            assignment_id="assignment-001",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )
    assert caught.value.reason == "assignment_id_reused"


def test_retry_assignment_offer_cannot_change_the_adjudicated_execution_spec() -> None:
    from qarunner.domain import RetryNotAllowed

    retry_queued, _, _, intent = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)

    with pytest.raises(RetryNotAllowed) as caught:
        retry_queued.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=_digest("unauthorized-spec"),
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )

    assert caught.value.reason == "retry_spec_mismatch"
    assert caught.value.adjudication_id == intent.adjudication_id
    assert retry_queued.assignment is None
    assert len(retry_queued.assignments) == 1
    assert len(retry_queued.attempts) == 1
    assert retry_queued.current_fence == 1


def test_retry_commit_creates_attempt2_fence2_and_explicit_provenance() -> None:
    from qarunner.domain import AssignmentState, RunState

    committed, worker2, _, intent = _committed_retry()
    run = committed.run
    attempt1, attempt2 = run.attempts

    assert committed.replayed is False
    assert committed.fence == 2
    assert attempt2 is committed.attempt
    assert attempt2.id == "attempt-002"
    assert attempt2.attempt_no == 2
    assert attempt2.fence == 2
    assert attempt2.worker == worker2.ref
    assert attempt2.events == ()
    assert attempt2.evidence is None
    assert attempt2.retry_provenance is not None
    assert attempt2.retry_provenance.retry_intent_id == intent.id
    assert attempt2.retry_provenance.retry_intent_digest == intent.digest
    assert attempt2.retry_provenance.source_attempt_id == attempt1.id
    assert attempt2.retry_provenance.source_attempt_no == attempt1.attempt_no
    assert attempt2.retry_provenance.source_fence == attempt1.fence
    assert attempt2.retry_provenance.adjudication_id == intent.adjudication_id
    assert attempt2.retry_provenance.adjudication_digest == intent.adjudication_digest
    assert attempt2.retry_provenance.decision == intent.decision
    assert run.state is RunState.RUNNING
    assert run.current_fence == 2
    assert run.pending_retry_intent is None
    assert run.retry_intents == (intent,)
    assert len(run.assignments) == 2
    assert run.assignments[0].id == "assignment-001"
    assert run.assignments[0].state is AssignmentState.COMMITTED
    assert run.assignments[1] == run.assignment
    assert run.assignment is not None
    assert run.assignment.state is AssignmentState.COMMITTED


def test_retry_commit_exact_replay_does_not_allocate_attempt3() -> None:
    committed, worker2, spec_digest, _ = _committed_retry()

    replay = committed.run.commit_start(
        assignment_id="assignment-002",
        worker=worker2.ref,
        start_commit_key="commit-002",
        spec_digest=spec_digest,
        new_attempt_id="must-not-be-used",
        observed_at=RETRY_COMMITTED_AT,
        expected_version=committed.run.version - 1,
    )

    assert replay.replayed is True
    assert replay.attempt is committed.attempt
    assert replay.fence == 2
    assert replay.run is committed.run
    assert len(replay.run.attempts) == 2


def test_superseded_commit_replay_cannot_reissue_old_fence_authority() -> None:
    from qarunner.domain import StaleFence, WorkerRef

    committed, _, spec_digest, _ = _committed_retry()

    with pytest.raises(StaleFence) as caught:
        committed.run.commit_start(
            assignment_id="assignment-001",
            worker=WorkerRef(worker_id="worker-001", generation=3),
            start_commit_key="commit-001",
            spec_digest=spec_digest,
            new_attempt_id="ignored",
            observed_at=RETRY_COMMITTED_AT,
            expected_version=committed.run.version,
        )

    assert caught.value.current_fence == 2
    assert caught.value.received_fence == 1
    assert len(committed.run.attempts) == 2


@pytest.mark.parametrize("stage", ["retry-queued", "offered", "claimed"])
def test_revoked_commit_replay_is_rejected_before_attempt2_advances_fence(stage: str) -> None:
    from qarunner.domain import AssignmentConflict, WorkerRef

    retry_queued, _, spec_digest, _ = _queued_retry()
    candidate = retry_queued
    if stage != "retry-queued":
        worker2, authority2 = _ready_worker(generation=4)
        candidate = retry_queued.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )
        if stage == "claimed":
            candidate = candidate.claim_assignment(
                assignment_id="assignment-002",
                worker=worker2.ref,
                observed_at=RETRY_CLAIMED_AT,
                expected_version=candidate.version,
            )

    with pytest.raises(AssignmentConflict) as caught:
        candidate.commit_start(
            assignment_id="assignment-001",
            worker=WorkerRef(worker_id="worker-001", generation=3),
            start_commit_key="commit-001",
            spec_digest=spec_digest,
            new_attempt_id="ignored",
            observed_at=RETRY_COMMITTED_AT,
            expected_version=candidate.version,
        )

    assert caught.value.reason == "start_commit_superseded"
    assert candidate.current_fence == 1
    assert len(candidate.attempts) == 1


@pytest.mark.parametrize("state_name", ["PROVISIONING", "RUNNING", "UPLOADING"])
def test_current_nonterminal_attempt_commit_replay_remains_idempotent(state_name: str) -> None:
    from qarunner.domain import AttemptState

    run, worker, spec_digest = _committed_run()
    attempt = run.attempts[-1]
    provisioning = attempt.transition(AttemptState.PROVISIONING, expected_version=attempt.version)
    if state_name == "PROVISIONING":
        current = provisioning
    else:
        running = provisioning.transition(
            AttemptState.RUNNING,
            expected_version=provisioning.version,
        )
        current = (
            running
            if state_name == "RUNNING"
            else running.transition(
                AttemptState.UPLOADING,
                expected_version=running.version,
            )
        )
    advanced = replace(run, attempts=(current,))

    replay = advanced.commit_start(
        assignment_id="assignment-001",
        worker=worker.ref,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="ignored",
        observed_at=INITIAL_COMMITTED_AT,
        expected_version=0,
    )

    assert replay.replayed is True
    assert replay.attempt is current
    assert replay.fence == 1
    assert replay.run is advanced


def test_new_commit_rejects_a_historical_attempt_identity() -> None:
    from qarunner.domain import AttemptConflict

    retry_queued, _, spec_digest, _ = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    offered = retry_queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=retry_queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-002",
        worker=worker2.ref,
        observed_at=RETRY_CLAIMED_AT,
        expected_version=offered.version,
    )

    with pytest.raises(AttemptConflict) as caught:
        claimed.commit_start(
            assignment_id="assignment-002",
            worker=worker2.ref,
            start_commit_key="commit-002",
            spec_digest=spec_digest,
            new_attempt_id="attempt-001",
            observed_at=RETRY_COMMITTED_AT,
            expected_version=claimed.version,
        )

    assert caught.value.reason == "attempt_id_reused"
    assert len(claimed.attempts) == 1
    assert claimed.current_fence == 1


def test_retry_commit_requires_the_pending_retry_intent() -> None:
    from qarunner.domain import RetryNotAllowed

    retry_queued, _, spec_digest, _ = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    offered = retry_queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=retry_queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-002",
        worker=worker2.ref,
        observed_at=RETRY_CLAIMED_AT,
        expected_version=offered.version,
    )
    corrupted = _unsafe_run_replace(claimed, pending_retry_intent_id=None)

    with pytest.raises(RetryNotAllowed) as caught:
        corrupted.commit_start(
            assignment_id="assignment-002",
            worker=worker2.ref,
            start_commit_key="commit-002",
            spec_digest=spec_digest,
            new_attempt_id="attempt-002",
            observed_at=RETRY_COMMITTED_AT,
            expected_version=corrupted.version,
        )

    assert caught.value.reason == "retry_intent_not_pending"
    assert len(corrupted.attempts) == 1
    assert corrupted.current_fence == 1


def test_retry_commit_defensively_rejects_assignment_spec_tampering() -> None:
    from qarunner.domain import RetryNotAllowed

    retry_queued, _, spec_digest, intent = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    offered = retry_queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=retry_queued.version,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-002",
        worker=worker2.ref,
        observed_at=RETRY_CLAIMED_AT,
        expected_version=offered.version,
    )
    assignment2 = claimed.assignment
    assert assignment2 is not None
    tampered_spec = _digest("tampered-persisted-spec")
    tampered_assignment = replace(assignment2, spec_digest=tampered_spec)
    tampered = _unsafe_run_replace(
        claimed,
        assignments=(*claimed.assignments[:-1], tampered_assignment),
    )

    with pytest.raises(RetryNotAllowed) as caught:
        tampered.commit_start(
            assignment_id="assignment-002",
            worker=worker2.ref,
            start_commit_key="commit-002",
            spec_digest=tampered_spec,
            new_attempt_id="attempt-002",
            observed_at=RETRY_COMMITTED_AT,
            expected_version=tampered.version,
        )

    assert caught.value.reason == "retry_spec_mismatch"
    assert caught.value.adjudication_id == intent.adjudication_id
    assert len(tampered.attempts) == 1
    assert tampered.current_fence == 1
    assert tampered.pending_retry_intent == intent


def test_real_attempt2_fences_attempt1_events_and_evidence_without_mutation() -> None:
    from qarunner.domain import AttemptAuthority, AttemptEvent, StaleFence

    committed, worker2, _, _ = _committed_retry()
    attempt1, attempt2 = committed.run.attempts
    before = attempt1
    authority = AttemptAuthority(
        current_fence=attempt2.fence,
        current_worker=worker2.ref,
    )
    event = AttemptEvent(
        event_id="late-event-001",
        event_seq=1,
        event_type="late_old_attempt_fact",
        payload_digest=_digest("late-event"),
    )

    with pytest.raises(StaleFence):
        attempt1.record_event(
            event,
            authority=authority,
            worker=attempt1.worker,
            fence=attempt1.fence,
            expected_version=attempt1.version,
        )
    with pytest.raises(StaleFence):
        attempt1.finalize_evidence(
            proposal=None,  # type: ignore[arg-type]
            trusted_exit=None,
            case_summary=None,
            artifacts=(),
            requirements=None,  # type: ignore[arg-type]
            authority=authority,
            worker=attempt1.worker,
            fence=attempt1.fence,
            expected_version=attempt1.version,
        )

    assert attempt1 is before
    assert committed.run.attempts[0] is before
    assert attempt1.events == ()
    assert attempt1.evidence is None


@pytest.mark.parametrize("stage", ["retry-queued", "offered", "claimed"])
def test_revoked_unknown_attempt_rejects_new_events_before_fence2(stage: str) -> None:
    from qarunner.domain import (
        AttemptAuthority,
        AttemptEvent,
        AttemptEventRejected,
    )

    retry_queued, _, spec_digest, _ = _queued_retry()
    candidate = retry_queued
    if stage != "retry-queued":
        worker2, authority2 = _ready_worker(generation=4)
        candidate = retry_queued.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )
        if stage == "claimed":
            candidate = candidate.claim_assignment(
                assignment_id="assignment-002",
                worker=worker2.ref,
                observed_at=RETRY_CLAIMED_AT,
                expected_version=candidate.version,
            )
    attempt1 = candidate.attempts[0]
    event = AttemptEvent(
        event_id="late-event-before-fence2",
        event_seq=1,
        event_type="late_unknown_fact",
        payload_digest=_digest("late-event-before-fence2"),
    )
    authority = AttemptAuthority(
        current_fence=candidate.current_fence,
        current_worker=attempt1.worker,
    )

    with pytest.raises(AttemptEventRejected) as caught:
        attempt1.record_event(
            event,
            authority=authority,
            worker=attempt1.worker,
            fence=attempt1.fence,
            expected_version=attempt1.version,
        )

    assert caught.value.code == "attempt_event_rejected"
    assert caught.value.reason == "attempt_terminal"
    assert candidate.attempts[0] is attempt1
    assert attempt1.events == ()


def test_terminal_attempt_allows_only_exact_historical_event_replay() -> None:
    from qarunner.domain import AttemptAuthority, AttemptEvent

    retry_queued, _, _, _ = _queued_retry()
    attempt1 = retry_queued.attempts[0]
    event = AttemptEvent(
        event_id="historical-event-001",
        event_seq=1,
        event_type="historical_fact",
        payload_digest=_digest("historical-event"),
    )
    historical = replace(attempt1, events=(event,))
    authority = AttemptAuthority(
        current_fence=retry_queued.current_fence,
        current_worker=attempt1.worker,
    )

    replay = historical.record_event(
        event,
        authority=authority,
        worker=attempt1.worker,
        fence=attempt1.fence,
        expected_version=0,
    )

    assert replay is historical
    assert replay.events == (event,)


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"id": 123}, "id", "not_string", id="id-type"),
        pytest.param({"id": ""}, "id", "empty", id="id-empty"),
        pytest.param(
            {"source_attempt_no": True},
            "source_attempt_no",
            "not_integer",
            id="attempt-no-type",
        ),
        pytest.param(
            {"source_fence": 0},
            "source_fence",
            "not_positive",
            id="fence-positive",
        ),
        pytest.param(
            {"adjudication_digest": "bad"},
            "adjudication_digest",
            "not_digest",
            id="adjudication-digest",
        ),
        pytest.param({"decision": "bad"}, "decision", "unknown", id="decision"),
        pytest.param(
            {"execution_spec_digest": "bad"},
            "execution_spec_digest",
            "not_digest",
            id="spec-digest",
        ),
        pytest.param(
            {"created_at": "bad"},
            "created_at",
            "not_datetime",
            id="created-type",
        ),
        pytest.param(
            {"created_at": datetime(2026, 7, 12, 22)},
            "created_at",
            "not_utc",
            id="created-utc",
        ),
    ],
)
def test_retry_intent_rejects_invalid_authority_values(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    with pytest.raises(DomainValidationError) as caught:
        replace(_retry_intent(), **changes)

    assert caught.value.entity_type == "retry_intent"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"run_id": "run-999"}, id="run"),
        pytest.param({"source_attempt_no": 2}, id="attempt-no"),
        pytest.param({"source_fence": 2}, id="fence"),
        pytest.param({"adjudication_digest": _digest("wrong-adjudication")}, id="digest"),
        pytest.param(
            {"decision": "ACCEPT_DUPLICATE_RISK_THEN_RETRY"},
            id="decision",
        ),
        pytest.param({"execution_spec_digest": _digest("wrong-spec")}, id="spec"),
        pytest.param(
            {"created_at": datetime(2026, 7, 12, 20, 30, tzinfo=UTC)},
            id="time",
        ),
    ],
)
def test_retry_intent_must_exactly_bind_current_source_and_adjudication(
    changes: dict[str, object],
) -> None:
    from qarunner.domain import RetryNotAllowed, UnknownAdjudicationDecision

    adjudicated, _, _, adjudication = _adjudicated_run()
    if "decision" in changes:
        changes = {
            **changes,
            "decision": UnknownAdjudicationDecision[changes["decision"]],
        }
    intent = replace(_retry_intent(adjudication=adjudication), **changes)

    with pytest.raises(RetryNotAllowed) as caught:
        adjudicated.queue_adjudicated_retry(
            retry_intent=intent,
            expected_version=adjudicated.version,
        )

    assert caught.value.reason == "retry_intent_mismatch"
    assert adjudicated.retry_intents == ()
    assert adjudicated.current_fence == 1


def test_retry_queue_rejects_nonunknown_source_and_uncommitted_assignment() -> None:
    from qarunner.domain import RetryNotAllowed

    committed, _, _ = _committed_run()
    with pytest.raises(RetryNotAllowed) as nonunknown:
        committed.queue_adjudicated_retry(
            retry_intent=_retry_intent(),
            expected_version=committed.version,
        )
    assert nonunknown.value.reason == "source_attempt_not_unknown"

    adjudicated, _, _, adjudication = _adjudicated_run()
    missing_assignment = _unsafe_run_replace(adjudicated, current_assignment_id=None)
    with pytest.raises(RetryNotAllowed) as uncommitted:
        missing_assignment.queue_adjudicated_retry(
            retry_intent=_retry_intent(adjudication=adjudication),
            expected_version=missing_assignment.version,
        )
    assert uncommitted.value.reason == "source_assignment_not_committed"


def test_retry_queue_rejects_second_pending_intent_and_corrupt_queue_states() -> None:
    from qarunner.domain import RetryNotAllowed, RunState

    retry_queued, _, spec_digest, _ = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    second_intent = replace(
        _retry_intent(intent_id="retry-002"), created_at=datetime(2026, 7, 12, 23, tzinfo=UTC)
    )
    with pytest.raises(RetryNotAllowed) as duplicate:
        retry_queued.queue_adjudicated_retry(
            retry_intent=second_intent,
            expected_version=retry_queued.version,
        )
    assert duplicate.value.reason == "retry_already_queued"

    missing_pending = _unsafe_run_replace(retry_queued, pending_retry_intent_id=None)
    with pytest.raises(RetryNotAllowed) as missing:
        missing_pending.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=missing_pending.version,
        )
    assert missing.value.reason == "retry_intent_not_pending"

    wrong_state = _unsafe_run_replace(retry_queued, state=RunState.QUEUED)
    with pytest.raises(RetryNotAllowed) as queued:
        wrong_state.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=wrong_state.version,
        )
    assert queued.value.reason == "retry_intent_not_pending"


def test_first_commit_rejects_a_forged_retry_binding() -> None:
    from qarunner.domain import AssignmentState, RetryNotAllowed, RunState

    committed, worker, spec_digest = _committed_run()
    intent = _retry_intent()
    assignment = committed.assignment
    assert assignment is not None
    forged_assignment = replace(
        assignment,
        state=AssignmentState.CLAIMED,
        committed_at=None,
        retry_intent_id=intent.id,
    )
    forged = _unsafe_run_replace(
        committed,
        state=RunState.ASSIGNED,
        current_fence=0,
        assignments=(forged_assignment,),
        attempts=(),
        retry_intents=(intent,),
        pending_retry_intent_id=intent.id,
    )

    with pytest.raises(RetryNotAllowed) as caught:
        forged.commit_start(
            assignment_id=forged_assignment.id,
            worker=worker.ref,
            start_commit_key="forged-commit",
            spec_digest=spec_digest,
            new_attempt_id="attempt-forged",
            observed_at=INITIAL_COMMITTED_AT,
            expected_version=forged.version,
        )

    assert caught.value.reason == "retry_intent_not_pending"
    assert forged.attempts == ()
    assert forged.current_fence == 0


def test_assignment_and_attempt_rehydration_reject_invalid_retry_links() -> None:
    from qarunner.domain import DomainValidationError

    committed, _, _, _ = _committed_retry()
    attempt1, attempt2 = committed.run.attempts
    provenance = attempt2.retry_provenance
    assert provenance is not None
    assignment2 = committed.run.assignment
    assert assignment2 is not None

    with pytest.raises(DomainValidationError) as assignment_error:
        replace(assignment2, retry_intent_id="")
    assert assignment_error.value.entity_type == "assignment"
    assert assignment_error.value.field == "retry_intent_id"

    with pytest.raises(DomainValidationError) as bad_type:
        replace(attempt2, retry_provenance="bad")
    assert bad_type.value.reason == "invalid_type"

    with pytest.raises(DomainValidationError) as first_attempt:
        replace(attempt1, retry_provenance=provenance)
    assert first_attempt.value.reason == "not_allowed_for_first_attempt"

    with pytest.raises(DomainValidationError) as self_source:
        replace(
            attempt2,
            retry_provenance=replace(provenance, source_attempt_id=attempt2.id),
        )
    assert self_source.value.reason == "source_is_self"

    with pytest.raises(DomainValidationError) as bad_decision:
        replace(provenance, decision="bad")
    assert bad_decision.value.entity_type == "retry_provenance"
    assert bad_decision.value.field == "decision"
    assert bad_decision.value.reason == "unknown"

    with pytest.raises(DomainValidationError) as missing_provenance:
        replace(attempt2, retry_provenance=None)
    assert missing_provenance.value.field == "retry_provenance"
    assert missing_provenance.value.reason == "required_for_retry_attempt"


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param("assignments-list", "assignments", "not_tuple", id="assignments-list"),
        pytest.param("assignment-duplicate", "assignments", "duplicate_id", id="assignment-id"),
        pytest.param(
            "assignment-pointer",
            "current_assignment_id",
            "not_found",
            id="assignment-pointer",
        ),
        pytest.param("attempts-list", "attempts", "not_tuple", id="attempts-list"),
        pytest.param("attempt-duplicate", "attempts", "duplicate_id", id="attempt-id"),
        pytest.param("attempt-owner", "attempts", "run_mismatch", id="attempt-owner"),
        pytest.param(
            "attempt-number",
            "attempts",
            "attempt_no_not_contiguous",
            id="attempt-number",
        ),
        pytest.param(
            "attempt-number-bool",
            "attempts",
            "attempt_no_not_contiguous",
            id="attempt-number-bool",
        ),
        pytest.param(
            "attempt-fence",
            "attempts",
            "attempt_fence_not_contiguous",
            id="attempt-fence",
        ),
        pytest.param(
            "current-fence",
            "current_fence",
            "attempt_fence_mismatch",
            id="current-fence",
        ),
        pytest.param("intents-list", "retry_intents", "not_tuple", id="intents-list"),
        pytest.param("intent-duplicate", "retry_intents", "duplicate_id", id="intent-id"),
        pytest.param(
            "intent-pointer",
            "pending_retry_intent_id",
            "not_found",
            id="intent-pointer",
        ),
        pytest.param(
            "assignment-intent",
            "attempts",
            "retry_provenance_mismatch",
            id="assignment-intent",
        ),
        pytest.param(
            "provenance-source",
            "attempts",
            "retry_provenance_mismatch",
            id="provenance-source",
        ),
    ],
)
def test_run_rehydration_rejects_corrupt_normalized_history(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    committed, _, _, _ = _committed_retry()
    run = committed.run
    attempt1, attempt2 = run.attempts
    assignment1, assignment2 = run.assignments
    intent = run.retry_intents[0]
    provenance = attempt2.retry_provenance
    assert provenance is not None

    if case == "assignments-list":
        changes = {"assignments": list(run.assignments)}
    elif case == "assignment-duplicate":
        changes = {"assignments": (*run.assignments, assignment2)}
    elif case == "assignment-pointer":
        changes = {"current_assignment_id": "assignment-missing"}
    elif case == "attempts-list":
        changes = {"attempts": list(run.attempts)}
    elif case == "attempt-duplicate":
        changes = {"attempts": (*run.attempts, attempt2)}
    elif case == "attempt-owner":
        changes = {"attempts": (attempt1, replace(attempt2, run_id="run-999"))}
    elif case == "attempt-number":
        changes = {"attempts": (attempt1, replace(attempt2, attempt_no=3))}
    elif case == "attempt-number-bool":
        changes = {
            "attempts": (
                _unsafe_run_replace(attempt1, attempt_no=True),
                attempt2,
            )
        }
    elif case == "attempt-fence":
        changes = {"attempts": (attempt1, replace(attempt2, fence=3))}
    elif case == "current-fence":
        changes = {"current_fence": 3}
    elif case == "intents-list":
        changes = {"retry_intents": list(run.retry_intents)}
    elif case == "intent-duplicate":
        changes = {"retry_intents": (*run.retry_intents, intent)}
    elif case == "intent-pointer":
        changes = {"pending_retry_intent_id": "retry-missing"}
    elif case == "assignment-intent":
        changes = {
            "assignments": (
                assignment1,
                replace(assignment2, retry_intent_id="retry-missing"),
            )
        }
    else:
        changes = {
            "attempts": (
                attempt1,
                replace(
                    attempt2,
                    retry_provenance=replace(provenance, source_fence=99),
                ),
            )
        }

    with pytest.raises(DomainValidationError) as caught:
        replace(run, **changes)

    assert caught.value.entity_type == "run"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param(
            "retry-pending",
            "pending_retry_intent_id",
            "required_for_retry_queued",
            id="retry-pending",
        ),
        pytest.param(
            "retry-assignment",
            "current_assignment_id",
            "must_be_clear_for_retry_queued",
            id="retry-assignment",
        ),
        pytest.param(
            "assigned-pending",
            "pending_retry_intent_id",
            "required_for_retry_assignment",
            id="assigned-pending",
        ),
        pytest.param(
            "running-pending",
            "pending_retry_intent_id",
            "must_be_consumed_for_running",
            id="running-pending",
        ),
    ],
)
def test_run_rehydration_rejects_invalid_retry_state_pointers(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    retry_queued, _, spec_digest, _ = _queued_retry()
    if case == "retry-pending":
        source = retry_queued
        changes = {"pending_retry_intent_id": None}
    elif case == "retry-assignment":
        source = retry_queued
        changes = {"current_assignment_id": source.assignments[0].id}
    elif case == "assigned-pending":
        worker2, authority2 = _ready_worker(generation=4)
        source = retry_queued.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )
        changes = {"pending_retry_intent_id": None}
    else:
        source, current_intent = _adjudicated_attempt2_with_retry_intent()
        changes = {
            "retry_intents": (*source.retry_intents, current_intent),
            "pending_retry_intent_id": current_intent.id,
        }

    with pytest.raises(DomainValidationError) as caught:
        replace(source, **changes)

    assert caught.value.entity_type == "run"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"state": "running"}, "state", "unknown", id="state"),
        pytest.param({"id": ""}, "id", "invalid", id="id"),
        pytest.param({"version": -1}, "version", "invalid", id="version"),
        pytest.param({"version": True}, "version", "invalid", id="version-bool"),
        pytest.param({"current_fence": True}, "current_fence", "invalid", id="fence"),
        pytest.param(
            {"current_assignment_id": ""},
            "current_assignment_id",
            "invalid",
            id="assignment-pointer",
        ),
        pytest.param(
            {"pending_retry_intent_id": ""},
            "pending_retry_intent_id",
            "invalid",
            id="intent-pointer",
        ),
    ],
)
def test_run_rehydration_rejects_raw_state_and_counter_values(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    run = _committed_retry()[0].run

    with pytest.raises(DomainValidationError) as caught:
        replace(run, **changes)

    assert caught.value.entity_type == "run"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param(
            "no-retry",
            "retry_intents",
            "adjudication_not_authoritative",
            id="no-retry",
        ),
        pytest.param(
            "superseded",
            "retry_intents",
            "adjudication_not_authoritative",
            id="superseded",
        ),
        pytest.param(
            "before-adjudication",
            "retry_intents",
            "adjudication_not_authoritative",
            id="time",
        ),
        pytest.param(
            "unconsumed-intent",
            "retry_intents",
            "duplicate_authority",
            id="unconsumed",
        ),
        pytest.param(
            "phantom-assignment",
            "assignments",
            "historical_reservation_not_committed",
            id="phantom-assignment",
        ),
        pytest.param(
            "queued-regression",
            "state",
            "queued_has_attempt_history",
            id="queued-regression",
        ),
    ],
)
def test_run_rehydration_rejects_forged_retry_authority_and_orphan_history(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import (
        Assignment,
        DomainValidationError,
        RetryProvenance,
        RunState,
    )

    committed, worker2, _, _ = _committed_retry()
    run = committed.run
    assignment1, assignment2 = run.assignments
    attempt1, attempt2 = run.attempts
    original_intent = run.retry_intents[0]

    if case == "no-retry":
        record = _adjudication(decision_name="MARK_INFRA_FAILED_NO_RETRY")
        intent = _retry_intent(adjudication=record)
        source = replace(attempt1, adjudications=(record,))
        provenance = RetryProvenance.from_intent(intent)
        changes = {
            "retry_intents": (intent,),
            "attempts": (source, replace(attempt2, retry_provenance=provenance)),
        }
    elif case == "superseded":
        record = _adjudication(
            adjudication_id="adjudication-002",
            decision_name="MARK_INFRA_FAILED_NO_RETRY",
            supersedes_adjudication_id=original_intent.adjudication_id,
            occurred_at=datetime(2026, 7, 12, 21, 30, tzinfo=UTC),
        )
        changes = {
            "attempts": (
                replace(attempt1, adjudications=(*attempt1.adjudications, record)),
                attempt2,
            )
        }
    elif case == "before-adjudication":
        intent = replace(
            original_intent,
            created_at=datetime(2026, 7, 12, 20, 30, tzinfo=UTC),
        )
        provenance = RetryProvenance.from_intent(intent)
        changes = {
            "retry_intents": (intent,),
            "attempts": (attempt1, replace(attempt2, retry_provenance=provenance)),
        }
    elif case == "unconsumed-intent":
        extra = replace(
            original_intent,
            id="retry-extra",
            created_at=original_intent.created_at + timedelta(seconds=1),
        )
        changes = {"retry_intents": (original_intent, extra)}
    elif case == "phantom-assignment":
        phantom = Assignment.offer(
            assignment_id="assignment-phantom",
            worker=worker2.ref,
            spec_digest=attempt2.spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
        )
        changes = {"assignments": (assignment1, phantom, assignment2)}
    else:
        changes = {
            "state": RunState.QUEUED,
            "current_assignment_id": None,
        }

    with pytest.raises(DomainValidationError) as caught:
        replace(run, **changes)

    assert caught.value.entity_type == "run"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param("assignment-type", "assignments", "invalid_type", id="assignment-type"),
        pytest.param(
            "assignment-order",
            "current_assignment_id",
            "not_latest",
            id="assignment-order",
        ),
        pytest.param("intent-type", "retry_intents", "invalid_type", id="intent-type"),
        pytest.param(
            "intent-order",
            "pending_retry_intent_id",
            "not_latest",
            id="intent-order",
        ),
        pytest.param("attempt-type", "attempts", "invalid_type", id="attempt-type"),
        pytest.param(
            "commit-key",
            "attempts",
            "duplicate_start_commit_key",
            id="commit-key",
        ),
        pytest.param(
            "assignment-missing", "attempts", "assignment_missing", id="assignment-missing"
        ),
        pytest.param(
            "assignment-mismatch",
            "attempts",
            "assignment_mismatch",
            id="assignment-mismatch",
        ),
        pytest.param(
            "first-retry-link",
            "assignments",
            "retry_intent_mismatch",
            id="first-retry-link",
        ),
        pytest.param(
            "provenance-missing",
            "attempts",
            "retry_provenance_missing",
            id="provenance-missing",
        ),
        pytest.param("intent-run", "retry_intents", "source_mismatch", id="intent-run"),
        pytest.param(
            "intent-source",
            "retry_intents",
            "source_mismatch",
            id="intent-source",
        ),
        pytest.param(
            "assignment-retry-link",
            "assignments",
            "retry_intent_mismatch",
            id="assignment-retry-link",
        ),
        pytest.param(
            "committed-orphan",
            "assignments",
            "committed_without_attempt",
            id="committed-orphan",
        ),
        pytest.param("planned-history", "state", "planned_has_history", id="planned-history"),
        pytest.param(
            "queued-current",
            "current_assignment_id",
            "must_be_clear_for_queued",
            id="queued-current",
        ),
        pytest.param(
            "queued-pending",
            "pending_retry_intent_id",
            "must_be_clear_for_queued",
            id="queued-pending",
        ),
        pytest.param(
            "running-current",
            "current_assignment_id",
            "committed_assignment_required",
            id="running-current",
        ),
    ],
)
def test_run_rehydration_rejects_additional_cross_history_corruption(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import (
        Assignment,
        AssignmentState,
        DomainValidationError,
        RetryProvenance,
        Run,
        RunState,
    )

    committed, worker2, spec_digest, _ = _committed_retry()
    run = committed.run
    assignment1, assignment2 = run.assignments
    attempt1, attempt2 = run.attempts
    intent = run.retry_intents[0]
    if case == "assignment-type":
        source = run
        changes = {"assignments": (assignment1, "bad")}
    elif case == "assignment-order":
        source = run
        changes = {"current_assignment_id": assignment1.id}
    elif case == "intent-type":
        source = run
        changes = {"retry_intents": ("bad",)}
    elif case == "intent-order":
        extra = replace(
            intent,
            id="retry-extra",
            source_attempt_id="attempt-002",
            source_attempt_no=2,
            source_fence=2,
            adjudication_id="adjudication-extra",
            created_at=intent.created_at + timedelta(seconds=1),
        )
        source = run
        changes = {
            "retry_intents": (intent, extra),
            "pending_retry_intent_id": intent.id,
        }
    elif case == "attempt-type":
        source = run
        changes = {"attempts": (attempt1, "bad")}
    elif case == "commit-key":
        source = run
        changes = {
            "attempts": (
                attempt1,
                replace(attempt2, start_commit_key=attempt1.start_commit_key),
            )
        }
    elif case == "assignment-missing":
        source = run
        changes = {"assignments": (assignment2,)}
    elif case == "assignment-mismatch":
        source = run
        changes = {
            "assignments": (
                replace(
                    assignment1,
                    state=AssignmentState.CLAIMED,
                    committed_at=None,
                ),
                assignment2,
            )
        }
    elif case == "first-retry-link":
        source = run
        changes = {
            "assignments": (
                replace(assignment1, retry_intent_id=intent.id),
                assignment2,
            )
        }
    elif case == "provenance-missing":
        source = run
        changes = {
            "attempts": (
                attempt1,
                _unsafe_run_replace(attempt2, retry_provenance=None),
            )
        }
    elif case == "intent-run":
        changed_intent = replace(intent, run_id="run-999")
        source = run
        changes = {
            "retry_intents": (changed_intent,),
            "attempts": (
                attempt1,
                replace(
                    attempt2,
                    retry_provenance=RetryProvenance.from_intent(changed_intent),
                ),
            ),
        }
    elif case == "intent-source":
        source, _, _, intent = _queued_retry()
        changes = {"retry_intents": (replace(intent, source_attempt_no=2),)}
    elif case == "assignment-retry-link":
        retry_queued, _, _, _ = _queued_retry()
        offered = retry_queued.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=_ready_worker(generation=4)[1],
            spec_digest=spec_digest,
            offered_at=RETRY_OFFERED_AT,
            expires_at=RETRY_EXPIRES_AT,
            expected_version=retry_queued.version,
        )
        current = offered.assignment
        assert current is not None
        source = offered
        changes = {
            "assignments": (
                offered.assignments[0],
                replace(current, retry_intent_id="retry-missing"),
            )
        }
    elif case == "committed-orphan":
        orphan = (
            Assignment.offer(
                assignment_id="assignment-orphan",
                worker=worker2.ref,
                spec_digest=spec_digest,
                offered_at=RETRY_OFFERED_AT,
                expires_at=RETRY_EXPIRES_AT,
            )
            .claim(claimed_at=RETRY_CLAIMED_AT)
            .commit(committed_at=RETRY_COMMITTED_AT)
        )
        source = run
        changes = {"assignments": (assignment1, orphan, assignment2)}
    elif case == "planned-history":
        source = run
        changes = {"state": RunState.PLANNED}
    elif case == "queued-current":
        worker, authority = _ready_worker(generation=3)
        queued = Run.create(run_id="run-queued").transition(RunState.QUEUED, expected_version=0)
        source = queued.offer_assignment(
            assignment_id="assignment-queued",
            worker=worker,
            worker_authority=authority,
            spec_digest=_digest("queued-spec"),
            offered_at=INITIAL_OFFERED_AT,
            expires_at=INITIAL_EXPIRES_AT,
            expected_version=queued.version,
        )
        changes = {"state": RunState.QUEUED}
    elif case == "queued-pending":
        source, _, _, _ = _queued_retry()
        changes = {"state": RunState.QUEUED}
    else:
        source = run
        changes = {"current_assignment_id": None}

    with pytest.raises(DomainValidationError) as caught:
        replace(source, **changes)

    assert caught.value.entity_type == "run"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_assigned_state_requires_current_reservation_and_matching_retry_intent() -> None:
    from qarunner.domain import DomainValidationError, Run, RunState

    queued = Run.create(run_id="run-empty-assigned").transition(
        RunState.QUEUED,
        expected_version=0,
    )
    with pytest.raises(DomainValidationError) as missing:
        replace(queued, state=RunState.ASSIGNED)
    assert missing.value.field == "current_assignment_id"
    assert missing.value.reason == "active_assignment_required"

    retry_queued, _, spec_digest, _ = _queued_retry()
    worker2, authority2 = _ready_worker(generation=4)
    offered = retry_queued.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=spec_digest,
        offered_at=RETRY_OFFERED_AT,
        expires_at=RETRY_EXPIRES_AT,
        expected_version=retry_queued.version,
    )
    current = offered.assignment
    assert current is not None
    with pytest.raises(DomainValidationError) as mismatch:
        replace(
            offered,
            assignments=(
                offered.assignments[0],
                replace(current, retry_intent_id=None),
            ),
        )
    assert mismatch.value.field == "current_assignment_id"
    assert mismatch.value.reason == "retry_intent_mismatch"


def test_run_rehydration_binds_assignment_order_and_current_to_attempt_order() -> None:
    from qarunner.domain import DomainValidationError

    run = _committed_retry()[0].run
    assignment1, assignment2 = run.assignments

    with pytest.raises(DomainValidationError) as caught:
        replace(
            run,
            assignments=(assignment2, assignment1),
            current_assignment_id=assignment1.id,
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason == "attempt_order_mismatch"


def test_run_rehydration_rejects_reauthorizing_old_adjudication_as_pending() -> None:
    from qarunner.domain import Assignment, DomainValidationError, RunState

    committed, worker2, _, _ = _committed_retry()
    run = committed.run
    original = run.retry_intents[0]
    duplicate = replace(
        original,
        id="retry-002",
        created_at=original.created_at + timedelta(hours=1),
    )
    assignment3 = Assignment.offer(
        assignment_id="assignment-003",
        worker=worker2.ref,
        spec_digest=duplicate.execution_spec_digest,
        offered_at=datetime(2026, 7, 13, 0, 1, tzinfo=UTC),
        expires_at=datetime(2026, 7, 13, 1, 1, tzinfo=UTC),
        retry_intent_id=duplicate.id,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            run,
            state=RunState.ASSIGNED,
            assignments=(*run.assignments, assignment3),
            current_assignment_id=assignment3.id,
            retry_intents=(*run.retry_intents, duplicate),
            pending_retry_intent_id=duplicate.id,
        )

    assert caught.value.field == "pending_retry_intent_id"
    assert caught.value.reason == "source_attempt_not_current"


def test_run_rehydration_rejects_distinct_authoritative_but_unconsumed_intent() -> None:
    from qarunner.domain import DomainValidationError

    adjudicated, orphan = _adjudicated_attempt2_with_retry_intent()

    with pytest.raises(DomainValidationError) as caught:
        replace(adjudicated, retry_intents=(*adjudicated.retry_intents, orphan))

    assert caught.value.field == "retry_intents"
    assert caught.value.reason == "order_mismatch"


@pytest.mark.parametrize(
    ("entity", "changes", "field", "reason"),
    [
        pytest.param("assignment", {"id": ""}, "id", "empty", id="assignment-id"),
        pytest.param(
            "assignment",
            {"id": 123},
            "id",
            "not_string",
            id="assignment-id-type",
        ),
        pytest.param(
            "assignment",
            {"worker": "bad"},
            "worker",
            "invalid_type",
            id="assignment-worker",
        ),
        pytest.param(
            "assignment",
            {"spec_digest": "bad"},
            "spec_digest",
            "not_digest",
            id="assignment-spec",
        ),
        pytest.param(
            "assignment",
            {"state": "claimed"},
            "state",
            "unknown",
            id="assignment-state",
        ),
        pytest.param("attempt", {"id": ""}, "id", "empty", id="attempt-id"),
        pytest.param(
            "attempt",
            {"id": 123},
            "id",
            "not_string",
            id="attempt-id-type",
        ),
        pytest.param("attempt", {"run_id": ""}, "run_id", "empty", id="attempt-run"),
        pytest.param(
            "attempt",
            {"assignment_id": ""},
            "assignment_id",
            "empty",
            id="attempt-assignment",
        ),
        pytest.param(
            "attempt",
            {"start_commit_key": ""},
            "start_commit_key",
            "empty",
            id="attempt-key",
        ),
        pytest.param(
            "attempt",
            {"attempt_no": True},
            "attempt_no",
            "not_integer",
            id="attempt-number-type",
        ),
        pytest.param(
            "attempt",
            {"fence": 0},
            "fence",
            "not_positive",
            id="attempt-fence",
        ),
        pytest.param(
            "attempt",
            {"worker": "bad"},
            "worker",
            "invalid_type",
            id="attempt-worker",
        ),
        pytest.param(
            "attempt",
            {"spec_digest": "bad"},
            "spec_digest",
            "not_digest",
            id="attempt-spec",
        ),
        pytest.param(
            "attempt",
            {"version": -1},
            "version",
            "invalid",
            id="attempt-version",
        ),
        pytest.param(
            "attempt",
            {"events": []},
            "events",
            "not_tuple",
            id="attempt-events-tuple",
        ),
        pytest.param(
            "attempt",
            {"events": ("bad",)},
            "events",
            "invalid_type",
            id="attempt-event-type",
        ),
        pytest.param(
            "attempt",
            {"evidence": "bad"},
            "evidence",
            "invalid_type",
            id="attempt-evidence",
        ),
    ],
)
def test_assignment_and_attempt_rehydration_reject_raw_authority_primitives(
    entity: str,
    changes: dict[str, object],
    field: str,
    reason: str,
) -> None:
    from qarunner.domain import DomainValidationError

    run = _committed_retry()[0].run
    source = run.assignment if entity == "assignment" else run.attempts[-1]
    assert source is not None

    with pytest.raises(DomainValidationError) as caught:
        replace(source, **changes)

    assert caught.value.entity_type == entity
    assert caught.value.field == field
    assert caught.value.reason == reason
