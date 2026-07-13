"""T-M0-ASSIGNMENT-001: pre-commit closure permits safe reassignment."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

EXPIRES_AT = datetime(2026, 7, 14, 0, tzinfo=UTC)
OFFERED_AT = EXPIRES_AT - timedelta(hours=1)
BEFORE_EXPIRY = EXPIRES_AT - timedelta(seconds=1)
AT_EXPIRY = EXPIRES_AT


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-assignment-closure.v1",
        payload={"label": label},
    )


def _ready_worker(*, generation: int = 3):
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


def _offered_initial(*, assignment_id: str = "assignment-001", generation: int = 3):
    from qarunner.domain import Run, RunState

    worker, authority = _ready_worker(generation=generation)
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id=assignment_id,
        worker=worker,
        worker_authority=authority,
        spec_digest=_digest("execution-spec"),
        offered_at=OFFERED_AT,
        expires_at=EXPIRES_AT,
        expected_version=queued.version,
    )
    return offered, worker


def _claimed_initial():
    offered, worker = _offered_initial()
    claimed = offered.claim_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    return claimed, worker


def _committed_initial():
    claimed, worker = _claimed_initial()
    committed = claimed.commit_start(
        assignment_id="assignment-001",
        worker=worker.ref,
        start_commit_key="commit-001",
        spec_digest=_digest("execution-spec"),
        new_attempt_id="attempt-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )
    return committed, worker


def _retry_queued():
    from tests.unit.domain.test_unknown_adjudicated_retry import _queued_retry

    return _queued_retry()


def _offered_retry(*, assignment_id: str = "assignment-002", generation: int = 4):
    retry_queued, _, spec_digest, intent = _retry_queued()
    worker, authority = _ready_worker(generation=generation)
    offered = retry_queued.offer_assignment(
        assignment_id=assignment_id,
        worker=worker,
        worker_authority=authority,
        spec_digest=spec_digest,
        offered_at=OFFERED_AT,
        expires_at=EXPIRES_AT,
        expected_version=retry_queued.version,
    )
    return offered, worker, spec_digest, intent


@pytest.mark.parametrize("claim_first", [False, True], ids=["offered", "claimed"])
def test_expiry_closes_precommit_assignment_without_attempt(claim_first: bool) -> None:
    from qarunner.domain import AssignmentClosureKind, AssignmentState, RunState

    offered, worker = _offered_initial()
    source = (
        offered.claim_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            observed_at=BEFORE_EXPIRY,
            expected_version=offered.version,
        )
        if claim_first
        else offered
    )

    expired = source.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=source.version,
    )

    historical = expired.assignments[-1]
    assert expired.state is RunState.QUEUED
    assert expired.assignment is None
    assert expired.current_assignment_id is None
    assert expired.attempts == ()
    assert expired.current_fence == 0
    assert expired.version == source.version + 1
    assert historical.state is AssignmentState.EXPIRED_PRESTART
    assert historical.closure is not None
    assert historical.closure.kind is AssignmentClosureKind.EXPIRED_PRESTART
    assert historical.closure.assignment_id == historical.id
    assert historical.closure.idempotency_key == "expiry-001"
    assert historical.closure.effective_at == AT_EXPIRY
    assert historical.closure.recorded_at == AT_EXPIRY
    assert historical.closure.worker is None


@pytest.mark.parametrize("claim_first", [False, True], ids=["offered", "claimed"])
def test_bound_worker_can_release_precommit_assignment(claim_first: bool) -> None:
    from qarunner.domain import AssignmentClosureKind, AssignmentState, RunState

    offered, worker = _offered_initial()
    source = (
        offered.claim_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            observed_at=BEFORE_EXPIRY,
            expected_version=offered.version,
        )
        if claim_first
        else offered
    )

    released = source.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=source.version,
    )

    historical = released.assignments[-1]
    assert released.state is RunState.QUEUED
    assert released.assignment is None
    assert released.attempts == ()
    assert released.current_fence == 0
    assert historical.state is AssignmentState.RELEASED_PRESTART
    assert historical.closure is not None
    assert historical.closure.kind is AssignmentClosureKind.RELEASED_PRESTART
    assert historical.closure.worker == worker.ref


def test_expiry_before_frozen_deadline_is_rejected_without_mutation() -> None:
    from qarunner.domain import AssignmentConflict

    offered, _ = _offered_initial()

    with pytest.raises(AssignmentConflict) as caught:
        offered.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=offered.version,
        )

    assert caught.value.reason == "assignment_not_expired"
    assert offered.assignment is not None
    assert offered.assignment.closure is None
    assert offered.attempts == ()
    assert offered.current_fence == 0


@pytest.mark.parametrize(
    "observed_at",
    [AT_EXPIRY, AT_EXPIRY + timedelta(seconds=1)],
    ids=["at-boundary", "after-boundary"],
)
def test_worker_release_cannot_override_expiry_authority(observed_at: datetime) -> None:
    from qarunner.domain import AssignmentConflict

    offered, worker = _offered_initial()

    with pytest.raises(AssignmentConflict) as caught:
        offered.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            release_key="release-001",
            observed_at=observed_at,
            expected_version=offered.version,
        )

    assert caught.value.reason == "assignment_expired"
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=observed_at,
        expected_version=offered.version,
    )
    assert expired.assignments[-1].state.value == "expired_prestart"


def test_claim_at_expiry_is_rejected_without_claiming() -> None:
    from qarunner.domain import AssignmentConflict, AssignmentState

    offered, worker = _offered_initial()

    with pytest.raises(AssignmentConflict) as caught:
        offered.claim_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            observed_at=AT_EXPIRY,
            expected_version=offered.version,
        )

    assert caught.value.reason == "assignment_expired"
    assert offered.assignment is not None
    assert offered.assignment.state is AssignmentState.OFFERED


def test_commit_at_expiry_is_rejected_without_execution_identity() -> None:
    from qarunner.domain import AssignmentConflict

    claimed, worker = _claimed_initial()

    with pytest.raises(AssignmentConflict) as caught:
        claimed.commit_start(
            assignment_id="assignment-001",
            worker=worker.ref,
            start_commit_key="commit-001",
            spec_digest=_digest("execution-spec"),
            new_attempt_id="attempt-001",
            observed_at=AT_EXPIRY,
            expected_version=claimed.version,
        )

    assert caught.value.reason == "assignment_expired"
    assert claimed.attempts == ()
    assert claimed.current_fence == 0


def test_successful_commit_replays_after_expiry_before_cas() -> None:
    committed, worker = _committed_initial()

    replay = committed.run.commit_start(
        assignment_id="assignment-001",
        worker=worker.ref,
        start_commit_key="commit-001",
        spec_digest=_digest("execution-spec"),
        new_attempt_id="must-not-be-used",
        observed_at=AT_EXPIRY + timedelta(hours=1),
        expected_version=0,
    )

    assert replay.replayed is True
    assert replay.run is committed.run
    assert replay.attempt is committed.attempt


@pytest.mark.parametrize("kind", ["expiry", "release"])
def test_exact_closure_replay_survives_safe_reassignment_and_stale_cas(kind: str) -> None:
    first, worker1 = _offered_initial()
    if kind == "expiry":
        closed = first.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="closure-001",
            observed_at=AT_EXPIRY,
            expected_version=first.version,
        )
    else:
        closed = first.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker1.ref,
            release_key="closure-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=first.version,
        )
    worker2, authority2 = _ready_worker(generation=4)
    reassigned = closed.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=_digest("execution-spec"),
        offered_at=AT_EXPIRY,
        expires_at=EXPIRES_AT + timedelta(hours=1),
        expected_version=closed.version,
    )

    if kind == "expiry":
        replay = reassigned.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="closure-001",
            observed_at=AT_EXPIRY,
            expected_version=0,
        )
    else:
        replay = reassigned.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker1.ref,
            release_key="closure-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=0,
        )

    assert replay is reassigned
    assert replay.assignment is not None
    assert replay.assignment.id == "assignment-002"
    assert replay.current_fence == 0
    assert replay.attempts == ()


def test_same_expiry_key_with_new_sweeper_time_replays_before_cas() -> None:
    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )

    replay = expired.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY + timedelta(seconds=1),
        expected_version=0,
    )

    assert replay is expired
    assert replay.assignments[-1].closure is not None
    assert replay.assignments[-1].closure.recorded_at == AT_EXPIRY


def test_same_release_key_with_new_server_time_replays_before_cas() -> None:
    offered, worker = _offered_initial()
    released = offered.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=OFFERED_AT + timedelta(minutes=30),
        expected_version=offered.version,
    )

    replay = released.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=0,
    )

    assert replay is released
    assert replay.assignments[-1].closure is not None
    assert replay.assignments[-1].closure.recorded_at == OFFERED_AT + timedelta(minutes=30)


def test_same_release_key_from_different_worker_conflicts_before_cas() -> None:
    from qarunner.domain import IdempotencyConflict, WorkerRef

    offered, worker = _offered_initial()
    released = offered.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )

    with pytest.raises(IdempotencyConflict) as caught:
        released.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=WorkerRef(worker_id="worker-001", generation=4),
            release_key="release-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=0,
        )

    assert caught.value.scope == ("run:run-001:assignment:assignment-001:precommit-closure")


@pytest.mark.parametrize("first_kind", ["expiry", "release"])
def test_first_terminal_closure_absorbs_competing_sequential_command(first_kind: str) -> None:
    from qarunner.domain import AssignmentConflict

    offered, worker = _offered_initial()
    if first_kind == "expiry":
        closed = offered.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="first-001",
            observed_at=AT_EXPIRY,
            expected_version=offered.version,
        )

        def command():
            return closed.release_precommit_assignment(
                assignment_id="assignment-001",
                worker=worker.ref,
                release_key="second-001",
                observed_at=BEFORE_EXPIRY,
                expected_version=closed.version,
            )
    else:
        closed = offered.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            release_key="first-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=offered.version,
        )

        def command():
            return closed.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="second-001",
                observed_at=AT_EXPIRY,
                expected_version=closed.version,
            )

    with pytest.raises(AssignmentConflict) as caught:
        command()

    assert caught.value.reason == "assignment_already_closed"
    assert closed.current_fence == 0
    assert closed.attempts == ()


def test_release_rejects_wrong_worker_generation() -> None:
    from qarunner.domain import AssignmentConflict, WorkerRef

    offered, _ = _offered_initial()

    with pytest.raises(AssignmentConflict) as caught:
        offered.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=WorkerRef(worker_id="worker-001", generation=4),
            release_key="release-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=offered.version,
        )

    assert caught.value.reason == "worker_mismatch"
    assert offered.assignment is not None
    assert offered.assignment.closure is None


def test_release_before_offer_window_is_rejected() -> None:
    from qarunner.domain import AssignmentConflict

    offered, worker = _offered_initial()

    with pytest.raises(AssignmentConflict) as caught:
        offered.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            release_key="release-001",
            observed_at=OFFERED_AT - timedelta(seconds=1),
            expected_version=offered.version,
        )

    assert caught.value.reason == "assignment_not_effective"
    assert offered.assignment is not None
    assert offered.assignment.closure is None


def test_claimed_assignment_rejects_release_recorded_before_claim() -> None:
    from qarunner.domain import AssignmentConflict

    claimed, worker = _claimed_initial()

    with pytest.raises(AssignmentConflict) as caught:
        claimed.release_precommit_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            release_key="release-001",
            observed_at=OFFERED_AT + timedelta(minutes=30),
            expected_version=claimed.version,
        )

    assert caught.value.reason == "release_before_claim"
    assert claimed.assignment is not None
    assert claimed.assignment.closure is None


@pytest.mark.parametrize("kind", ["expiry", "release"])
def test_precommit_closure_obeys_run_cas(kind: str) -> None:
    from qarunner.domain import VersionConflict

    offered, worker = _offered_initial()

    with pytest.raises(VersionConflict):
        if kind == "expiry":
            offered.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="closure-001",
                observed_at=AT_EXPIRY,
                expected_version=offered.version - 1,
            )
        else:
            offered.release_precommit_assignment(
                assignment_id="assignment-001",
                worker=worker.ref,
                release_key="closure-001",
                observed_at=BEFORE_EXPIRY,
                expected_version=offered.version - 1,
            )


@pytest.mark.parametrize("kind", ["expiry", "release"])
def test_committed_assignment_cannot_use_precommit_closure(kind: str) -> None:
    from qarunner.domain import AssignmentConflict

    committed, worker = _committed_initial()

    with pytest.raises(AssignmentConflict) as caught:
        if kind == "expiry":
            committed.run.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="closure-001",
                observed_at=AT_EXPIRY,
                expected_version=committed.run.version,
            )
        else:
            committed.run.release_precommit_assignment(
                assignment_id="assignment-001",
                worker=worker.ref,
                release_key="closure-001",
                observed_at=BEFORE_EXPIRY,
                expected_version=committed.run.version,
            )

    assert caught.value.reason == "assignment_not_precommit"
    assert committed.run.attempts == (committed.attempt,)
    assert committed.run.current_fence == 1


def test_closed_assignment_can_be_safely_reassigned_and_first_commit_remains_fence_one() -> None:
    from qarunner.domain import AssignmentState

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    worker2, authority2 = _ready_worker(generation=4)
    offered2 = expired.offer_assignment(
        assignment_id="assignment-002",
        worker=worker2,
        worker_authority=authority2,
        spec_digest=_digest("execution-spec"),
        offered_at=AT_EXPIRY,
        expires_at=EXPIRES_AT + timedelta(hours=1),
        expected_version=expired.version,
    )
    claimed2 = offered2.claim_assignment(
        assignment_id="assignment-002",
        worker=worker2.ref,
        observed_at=AT_EXPIRY,
        expected_version=offered2.version,
    )
    committed = claimed2.commit_start(
        assignment_id="assignment-002",
        worker=worker2.ref,
        start_commit_key="commit-002",
        spec_digest=_digest("execution-spec"),
        new_attempt_id="attempt-001",
        observed_at=AT_EXPIRY,
        expected_version=claimed2.version,
    )

    assert tuple(item.state for item in committed.run.assignments) == (
        AssignmentState.EXPIRED_PRESTART,
        AssignmentState.COMMITTED,
    )
    assert committed.attempt.assignment_id == "assignment-002"
    assert committed.attempt.attempt_no == 1
    assert committed.fence == 1


def test_closed_assignment_rejects_late_commit_without_creating_attempt() -> None:
    from qarunner.domain import AssignmentConflict

    claimed, worker = _claimed_initial()
    released = claimed.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=claimed.version,
    )

    with pytest.raises(AssignmentConflict) as caught:
        released.commit_start(
            assignment_id="assignment-001",
            worker=worker.ref,
            start_commit_key="commit-001",
            spec_digest=_digest("execution-spec"),
            new_attempt_id="attempt-001",
            observed_at=BEFORE_EXPIRY,
            expected_version=released.version,
        )

    assert caught.value.reason == "assignment_not_claimed"
    assert released.attempts == ()
    assert released.current_fence == 0


def test_closed_assignment_id_cannot_be_reused() -> None:
    from qarunner.domain import AssignmentConflict

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    worker2, authority2 = _ready_worker(generation=4)

    with pytest.raises(AssignmentConflict) as caught:
        expired.offer_assignment(
            assignment_id="assignment-001",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=_digest("execution-spec"),
            offered_at=AT_EXPIRY,
            expires_at=EXPIRES_AT + timedelta(hours=1),
            expected_version=expired.version,
        )

    assert caught.value.reason == "assignment_id_reused"


@pytest.mark.parametrize("kind", ["expiry", "release"])
def test_retry_assignment_closure_preserves_pending_authority(kind: str) -> None:
    from qarunner.domain import RunState

    offered, worker, _, intent = _offered_retry()
    if kind == "expiry":
        closed = offered.expire_precommit_assignment(
            assignment_id="assignment-002",
            expiry_key="closure-002",
            observed_at=AT_EXPIRY,
            expected_version=offered.version,
        )
    else:
        closed = offered.release_precommit_assignment(
            assignment_id="assignment-002",
            worker=worker.ref,
            release_key="closure-002",
            observed_at=BEFORE_EXPIRY,
            expected_version=offered.version,
        )

    assert closed.state is RunState.RETRY_QUEUED
    assert closed.assignment is None
    assert closed.pending_retry_intent is intent
    assert len(closed.attempts) == 1
    assert closed.current_fence == 1


def test_retry_assignment_can_close_twice_then_commit_one_new_attempt() -> None:
    from qarunner.domain import AssignmentState

    offered2, _, spec_digest, intent = _offered_retry()
    expired2 = offered2.expire_precommit_assignment(
        assignment_id="assignment-002",
        expiry_key="expiry-002",
        observed_at=AT_EXPIRY,
        expected_version=offered2.version,
    )
    worker3, authority3 = _ready_worker(generation=5)
    offered3 = expired2.offer_assignment(
        assignment_id="assignment-003",
        worker=worker3,
        worker_authority=authority3,
        spec_digest=spec_digest,
        offered_at=AT_EXPIRY,
        expires_at=EXPIRES_AT + timedelta(hours=1),
        expected_version=expired2.version,
    )
    released3 = offered3.release_precommit_assignment(
        assignment_id="assignment-003",
        worker=worker3.ref,
        release_key="release-003",
        observed_at=AT_EXPIRY,
        expected_version=offered3.version,
    )
    worker4, authority4 = _ready_worker(generation=6)
    offered4 = released3.offer_assignment(
        assignment_id="assignment-004",
        worker=worker4,
        worker_authority=authority4,
        spec_digest=spec_digest,
        offered_at=AT_EXPIRY,
        expires_at=EXPIRES_AT + timedelta(hours=2),
        expected_version=released3.version,
    )
    claimed4 = offered4.claim_assignment(
        assignment_id="assignment-004",
        worker=worker4.ref,
        observed_at=AT_EXPIRY,
        expected_version=offered4.version,
    )
    committed = claimed4.commit_start(
        assignment_id="assignment-004",
        worker=worker4.ref,
        start_commit_key="commit-002",
        spec_digest=spec_digest,
        new_attempt_id="attempt-002",
        observed_at=AT_EXPIRY,
        expected_version=claimed4.version,
    )

    assert tuple(item.state for item in committed.run.assignments[-3:]) == (
        AssignmentState.EXPIRED_PRESTART,
        AssignmentState.RELEASED_PRESTART,
        AssignmentState.COMMITTED,
    )
    assert {item.retry_intent_id for item in committed.run.assignments[-3:]} == {intent.id}
    assert len(committed.run.attempts) == 2
    assert committed.attempt.attempt_no == 2
    assert committed.fence == 2


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        pytest.param("2026-07-14T00:00:00Z", "not_datetime", id="not-datetime"),
        pytest.param(datetime(2026, 7, 14), "not_utc", id="naive"),
        pytest.param(
            datetime(2026, 7, 13, 19, tzinfo=timezone(timedelta(hours=-5))),
            "not_utc",
            id="offset",
        ),
    ],
)
def test_assignment_expiry_requires_utc_datetime(value: object, reason: str) -> None:
    from qarunner.domain import Assignment, DomainValidationError, WorkerRef

    with pytest.raises(DomainValidationError) as caught:
        Assignment.offer(
            assignment_id="assignment-001",
            worker=WorkerRef(worker_id="worker-001", generation=3),
            spec_digest=_digest("execution-spec"),
            offered_at=OFFERED_AT,
            expires_at=value,
        )

    assert caught.value.entity_type == "assignment"
    assert caught.value.field == "expires_at"
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("observed_at", "reason"),
    [
        pytest.param("now", "not_datetime", id="not-datetime"),
        pytest.param(datetime(2026, 7, 13, 23, 30), "not_utc", id="not-utc"),
    ],
)
def test_claim_requires_trusted_utc_observation(observed_at: object, reason: str) -> None:
    from qarunner.domain import DomainValidationError

    offered, worker = _offered_initial()

    with pytest.raises(DomainValidationError) as caught:
        offered.claim_assignment(
            assignment_id="assignment-001",
            worker=worker.ref,
            observed_at=observed_at,
            expected_version=offered.version,
        )

    assert caught.value.entity_type == "run"
    assert caught.value.field == "observed_at"
    assert caught.value.reason == reason


@pytest.mark.parametrize("kind", ["expiry", "release"])
@pytest.mark.parametrize(
    ("observed_at", "reason"),
    [
        pytest.param("now", "not_datetime", id="not-datetime"),
        pytest.param(datetime(2026, 7, 14), "not_utc", id="naive"),
        pytest.param(
            datetime(2026, 7, 13, 19, tzinfo=timezone(timedelta(hours=-5))),
            "not_utc",
            id="offset",
        ),
    ],
)
def test_precommit_closure_requires_trusted_utc_observation(
    kind: str, observed_at: object, reason: str
) -> None:
    from qarunner.domain import DomainValidationError

    offered, worker = _offered_initial()

    with pytest.raises(DomainValidationError) as caught:
        if kind == "expiry":
            offered.expire_precommit_assignment(
                assignment_id="assignment-001",
                expiry_key="closure-001",
                observed_at=observed_at,
                expected_version=offered.version,
            )
        else:
            offered.release_precommit_assignment(
                assignment_id="assignment-001",
                worker=worker.ref,
                release_key="closure-001",
                observed_at=observed_at,
                expected_version=offered.version,
            )

    assert caught.value.entity_type == "run"
    assert caught.value.field == "observed_at"
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("changes", "field", "reason"),
    [
        pytest.param({"assignment_id": 1}, "assignment_id", "not_string", id="id-type"),
        pytest.param({"assignment_id": " "}, "assignment_id", "empty", id="id-empty"),
        pytest.param({"idempotency_key": 1}, "idempotency_key", "not_string", id="key-type"),
        pytest.param({"idempotency_key": " "}, "idempotency_key", "empty", id="key-empty"),
        pytest.param({"kind": "expired"}, "kind", "unknown", id="kind"),
        pytest.param(
            {"effective_at": "now"},
            "effective_at",
            "not_datetime",
            id="effective-type",
        ),
        pytest.param(
            {"effective_at": datetime(2026, 7, 14)},
            "effective_at",
            "not_utc",
            id="effective-naive",
        ),
        pytest.param(
            {"recorded_at": "now"},
            "recorded_at",
            "not_datetime",
            id="recorded-type",
        ),
        pytest.param(
            {"recorded_at": BEFORE_EXPIRY},
            "recorded_at",
            "before_effective_at",
            id="recorded-before-effective",
        ),
        pytest.param({"worker": "worker-001"}, "worker", "invalid_type", id="worker"),
    ],
)
def test_assignment_closure_rejects_invalid_primitive_values(
    changes: dict[str, object], field: str, reason: str
) -> None:
    from qarunner.domain import (
        AssignmentClosure,
        AssignmentClosureKind,
        DomainValidationError,
    )

    values = {
        "assignment_id": "assignment-001",
        "idempotency_key": "closure-001",
        "kind": AssignmentClosureKind.EXPIRED_PRESTART,
        "effective_at": AT_EXPIRY,
        "recorded_at": AT_EXPIRY,
        "worker": None,
    }

    with pytest.raises(DomainValidationError) as caught:
        AssignmentClosure(**(values | changes))

    assert caught.value.entity_type == "assignment_closure"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("kind_name", "worker", "field", "reason"),
    [
        pytest.param(
            "EXPIRED_PRESTART",
            "bound",
            "worker",
            "forbidden_for_kind",
            id="expired-worker",
        ),
        pytest.param(
            "RELEASED_PRESTART",
            None,
            "worker",
            "required_for_kind",
            id="released-worker",
        ),
    ],
)
def test_assignment_closure_kind_controls_worker_authority(
    kind_name: str, worker: str | None, field: str, reason: str
) -> None:
    from qarunner.domain import (
        AssignmentClosure,
        AssignmentClosureKind,
        DomainValidationError,
        WorkerRef,
    )

    worker_ref = WorkerRef(worker_id="worker-001", generation=3) if worker else None

    with pytest.raises(DomainValidationError) as caught:
        AssignmentClosure(
            assignment_id="assignment-001",
            idempotency_key="closure-001",
            kind=AssignmentClosureKind[kind_name],
            effective_at=AT_EXPIRY,
            recorded_at=AT_EXPIRY,
            worker=worker_ref,
        )

    assert caught.value.field == field
    assert caught.value.reason == reason


def test_assignment_closure_separates_request_identity_from_server_times() -> None:
    from qarunner.domain import AssignmentClosure, AssignmentClosureKind

    first = AssignmentClosure(
        assignment_id="assignment-001",
        idempotency_key="expiry-001",
        kind=AssignmentClosureKind.EXPIRED_PRESTART,
        effective_at=AT_EXPIRY,
        recorded_at=AT_EXPIRY,
        worker=None,
    )
    delayed_record = replace(first, recorded_at=AT_EXPIRY + timedelta(seconds=5))

    assert first.request_digest == delayed_record.request_digest
    assert first.digest != delayed_record.digest


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param("expiry-window", "expires_at", "not_after_offered_at", id="window"),
        pytest.param("claimed-window", "claimed_at", "outside_offer_window", id="claim-time"),
        pytest.param(
            "committed-window",
            "committed_at",
            "outside_claim_window",
            id="commit-time",
        ),
        pytest.param("closure-type", "closure", "invalid_type", id="closure-type"),
        pytest.param("offered-history", "claimed_at", "forbidden_for_state", id="offered"),
        pytest.param("claimed-missing", "claimed_at", "required_for_state", id="claimed"),
        pytest.param("committed-missing", "committed_at", "required_for_state", id="committed"),
        pytest.param(
            "terminal-committed",
            "committed_at",
            "forbidden_for_state",
            id="terminal-commit",
        ),
        pytest.param("release-worker", "closure", "worker_mismatch", id="release-worker"),
        pytest.param("release-before-claim", "closure", "before_claimed_at", id="release-claim"),
        pytest.param("release-before", "closure", "before_offered_at", id="release-before"),
    ],
)
def test_assignment_rehydration_rejects_invalid_timeline_and_state_fields(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import (
        AssignmentClosure,
        AssignmentClosureKind,
        DomainValidationError,
        WorkerRef,
    )

    offered_run, worker = _offered_initial()
    offered = offered_run.assignment
    assert offered is not None
    claimed = offered.claim(claimed_at=BEFORE_EXPIRY)
    committed = claimed.commit(committed_at=BEFORE_EXPIRY)
    released = offered.close_prestart(
        AssignmentClosure(
            assignment_id=offered.id,
            idempotency_key="release-001",
            kind=AssignmentClosureKind.RELEASED_PRESTART,
            effective_at=BEFORE_EXPIRY,
            recorded_at=BEFORE_EXPIRY,
            worker=worker.ref,
        )
    )
    if case == "expiry-window":
        source, changes = offered, {"expires_at": OFFERED_AT}
    elif case == "claimed-window":
        source, changes = claimed, {"claimed_at": AT_EXPIRY}
    elif case == "committed-window":
        source, changes = committed, {"committed_at": AT_EXPIRY}
    elif case == "closure-type":
        source, changes = offered, {"closure": "bad"}
    elif case == "offered-history":
        source, changes = offered, {"claimed_at": BEFORE_EXPIRY}
    elif case == "claimed-missing":
        source, changes = claimed, {"claimed_at": None}
    elif case == "committed-missing":
        source, changes = committed, {"committed_at": None}
    elif case == "terminal-committed":
        source, changes = (
            released,
            {
                "claimed_at": BEFORE_EXPIRY,
                "committed_at": BEFORE_EXPIRY,
            },
        )
    elif case == "release-worker":
        source, changes = (
            released,
            {
                "closure": replace(
                    released.closure,
                    worker=WorkerRef(worker_id="worker-001", generation=4),
                )
            },
        )
    elif case == "release-before-claim":
        source, changes = (
            released,
            {
                "claimed_at": BEFORE_EXPIRY,
                "closure": replace(
                    released.closure,
                    effective_at=OFFERED_AT + timedelta(minutes=30),
                    recorded_at=OFFERED_AT + timedelta(minutes=30),
                ),
            },
        )
    else:
        source, changes = (
            released,
            {
                "closure": replace(
                    released.closure,
                    effective_at=OFFERED_AT - timedelta(seconds=1),
                    recorded_at=OFFERED_AT - timedelta(seconds=1),
                )
            },
        )

    with pytest.raises(DomainValidationError) as caught:
        replace(source, **changes)

    assert caught.value.entity_type == "assignment"
    assert caught.value.field == field
    assert caught.value.reason == reason


@pytest.mark.parametrize("operation", ["reclaim", "recommit", "reclose"])
def test_assignment_value_transitions_cannot_rewrite_durable_history(operation: str) -> None:
    from qarunner.domain import (
        AssignmentClosure,
        AssignmentClosureKind,
        DomainValidationError,
    )

    if operation == "reclaim":
        source, _ = _claimed_initial()
        assignment = source.assignment
        assert assignment is not None

        def command():
            return assignment.claim(claimed_at=BEFORE_EXPIRY - timedelta(seconds=1))

    elif operation == "recommit":
        committed, _ = _committed_initial()
        assignment = committed.run.assignment
        assert assignment is not None

        def command():
            return assignment.commit(committed_at=BEFORE_EXPIRY + timedelta(milliseconds=500))

    else:
        offered, worker = _offered_initial()
        expired = offered.expire_precommit_assignment(
            assignment_id="assignment-001",
            expiry_key="expiry-001",
            observed_at=AT_EXPIRY,
            expected_version=offered.version,
        )
        assignment = expired.assignments[-1]
        replacement = AssignmentClosure(
            assignment_id=assignment.id,
            idempotency_key="release-001",
            kind=AssignmentClosureKind.RELEASED_PRESTART,
            effective_at=BEFORE_EXPIRY,
            recorded_at=BEFORE_EXPIRY,
            worker=worker.ref,
        )

        def command():
            return assignment.close_prestart(replacement)

    with pytest.raises(DomainValidationError) as caught:
        command()

    assert caught.value.entity_type == "assignment"
    assert caught.value.field == "state"
    assert caught.value.reason == "transition_not_allowed"


@pytest.mark.parametrize(
    ("case", "field", "reason"),
    [
        pytest.param("terminal-without-closure", "closure", "required_for_state", id="missing"),
        pytest.param("active-with-closure", "closure", "forbidden_for_state", id="active"),
        pytest.param("closure-id-mismatch", "closure", "assignment_mismatch", id="id"),
        pytest.param("closure-kind-mismatch", "closure", "state_mismatch", id="kind"),
        pytest.param(
            "expired-too-early",
            "closure",
            "expiry_effective_at_mismatch",
            id="early",
        ),
        pytest.param("released-too-late", "closure", "at_or_after_expiry", id="late"),
    ],
)
def test_assignment_rehydration_rejects_inconsistent_closure(
    case: str, field: str, reason: str
) -> None:
    from qarunner.domain import (
        AssignmentClosure,
        AssignmentClosureKind,
        AssignmentState,
        DomainValidationError,
    )

    offered, worker = _offered_initial()
    assignment = offered.assignment
    assert assignment is not None
    expiry = AssignmentClosure(
        assignment_id=assignment.id,
        idempotency_key="expiry-001",
        kind=AssignmentClosureKind.EXPIRED_PRESTART,
        effective_at=AT_EXPIRY,
        recorded_at=AT_EXPIRY,
        worker=None,
    )
    release = AssignmentClosure(
        assignment_id=assignment.id,
        idempotency_key="release-001",
        kind=AssignmentClosureKind.RELEASED_PRESTART,
        effective_at=BEFORE_EXPIRY,
        recorded_at=BEFORE_EXPIRY,
        worker=worker.ref,
    )
    if case == "terminal-without-closure":
        changes = {"state": AssignmentState.EXPIRED_PRESTART}
    elif case == "active-with-closure":
        changes = {"closure": expiry}
    elif case == "closure-id-mismatch":
        changes = {
            "state": AssignmentState.EXPIRED_PRESTART,
            "closure": replace(expiry, assignment_id="assignment-other"),
        }
    elif case == "closure-kind-mismatch":
        changes = {"state": AssignmentState.EXPIRED_PRESTART, "closure": release}
    elif case == "expired-too-early":
        changes = {
            "state": AssignmentState.EXPIRED_PRESTART,
            "closure": replace(expiry, effective_at=BEFORE_EXPIRY),
        }
    else:
        changes = {
            "state": AssignmentState.RELEASED_PRESTART,
            "closure": replace(
                release,
                effective_at=AT_EXPIRY,
                recorded_at=AT_EXPIRY,
            ),
        }

    with pytest.raises(DomainValidationError) as caught:
        replace(assignment, **changes)

    assert caught.value.entity_type == "assignment"
    assert caught.value.field == field
    assert caught.value.reason == reason


def test_run_rehydration_rejects_closed_current_pointer() -> None:
    from qarunner.domain import DomainValidationError, RunState

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            expired,
            state=RunState.ASSIGNED,
            current_assignment_id="assignment-001",
        )

    assert caught.value.entity_type == "run"
    assert caught.value.field == "assignments"
    assert caught.value.reason == "closed_reservation_current"


def test_run_rehydration_rejects_phantom_nonretry_closure_after_attempt() -> None:
    from qarunner.domain import (
        Assignment,
        AssignmentClosure,
        AssignmentClosureKind,
        AssignmentState,
        DomainValidationError,
    )

    retry_queued, worker, spec_digest, _ = _retry_queued()
    phantom = Assignment(
        id="assignment-phantom",
        worker=worker.ref,
        spec_digest=spec_digest,
        state=AssignmentState.RELEASED_PRESTART,
        offered_at=AT_EXPIRY,
        expires_at=EXPIRES_AT + timedelta(hours=1),
        retry_intent_id=None,
        closure=AssignmentClosure(
            assignment_id="assignment-phantom",
            idempotency_key="release-phantom",
            kind=AssignmentClosureKind.RELEASED_PRESTART,
            effective_at=EXPIRES_AT,
            recorded_at=EXPIRES_AT,
            worker=worker.ref,
        ),
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            retry_queued,
            assignments=(*retry_queued.assignments, phantom),
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason == "committed_assignment_not_epoch_tail"


def test_run_rehydration_rejects_retry_assignment_group_regression() -> None:
    from qarunner.domain import DomainValidationError

    offered, _, _, _ = _offered_retry()
    closed = offered.expire_precommit_assignment(
        assignment_id="assignment-002",
        expiry_key="expiry-002",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    first = closed.assignments[0]
    retry = closed.assignments[-1]

    with pytest.raises(DomainValidationError) as caught:
        replace(
            closed,
            assignments=(retry, first),
            current_assignment_id=None,
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason in {"retry_intent_order_mismatch", "attempt_order_mismatch"}


def test_run_rehydration_rejects_assignment_spec_drift() -> None:
    from qarunner.domain import AssignmentState, DomainValidationError, RunState

    offered, worker = _offered_initial()
    released = offered.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    worker2, _ = _ready_worker(generation=4)
    assignment2 = replace(
        released.assignments[0],
        id="assignment-002",
        worker=worker2.ref,
        spec_digest=_digest("changed-execution-spec"),
        state=AssignmentState.OFFERED,
        offered_at=AT_EXPIRY,
        expires_at=AT_EXPIRY + timedelta(hours=1),
        closure=None,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            released,
            state=RunState.ASSIGNED,
            assignments=(*released.assignments, assignment2),
            current_assignment_id=assignment2.id,
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason == "spec_digest_mismatch"


def test_run_rehydration_rejects_assignment_timeline_regression() -> None:
    from qarunner.domain import AssignmentState, DomainValidationError, RunState

    offered, worker = _offered_initial()
    released = offered.release_precommit_assignment(
        assignment_id="assignment-001",
        worker=worker.ref,
        release_key="release-001",
        observed_at=BEFORE_EXPIRY,
        expected_version=offered.version,
    )
    worker2, _ = _ready_worker(generation=4)
    assignment2 = replace(
        released.assignments[0],
        id="assignment-002",
        worker=worker2.ref,
        state=AssignmentState.OFFERED,
        offered_at=OFFERED_AT + timedelta(minutes=30),
        expires_at=AT_EXPIRY + timedelta(hours=1),
        closure=None,
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            released,
            state=RunState.ASSIGNED,
            assignments=(*released.assignments, assignment2),
            current_assignment_id=assignment2.id,
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason == "timeline_not_monotonic"


def test_run_rehydration_rejects_offer_before_retry_authority_exists() -> None:
    from qarunner.domain import DomainValidationError

    offered, _, _, intent = _offered_retry()
    current = offered.assignment
    assert current is not None

    with pytest.raises(DomainValidationError) as caught:
        replace(
            offered,
            assignments=(
                offered.assignments[0],
                replace(
                    current,
                    offered_at=intent.created_at - timedelta(seconds=1),
                ),
            ),
        )

    assert caught.value.field == "assignments"
    assert caught.value.reason == "offered_before_retry_intent"


def test_run_rehydration_rejects_unknown_history_before_assignment_commit() -> None:
    from qarunner.domain import DomainValidationError

    retry_queued, _, _, intent = _retry_queued()
    assignment = retry_queued.assignments[0]
    attempt = retry_queued.attempts[0]
    assert assignment.committed_at is not None
    assert attempt.unknown_observation is not None
    assert attempt.adjudications
    observation = replace(
        attempt.unknown_observation,
        recorded_at=assignment.committed_at - timedelta(seconds=1),
    )
    adjudication = replace(
        attempt.adjudications[-1],
        unknown_observation_digest=observation.digest,
    )
    source = replace(
        attempt,
        unknown_observation=observation,
        adjudications=(adjudication,),
    )
    changed_intent = replace(intent, adjudication_digest=adjudication.digest)

    with pytest.raises(DomainValidationError) as caught:
        replace(
            retry_queued,
            attempts=(source,),
            retry_intents=(changed_intent,),
        )

    assert caught.value.entity_type == "run"
    assert caught.value.field == "attempts"
    assert caught.value.reason == "unknown_before_assignment_commit"


def test_initial_reassignment_cannot_drift_execution_spec() -> None:
    from qarunner.domain import AssignmentConflict

    offered, _ = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    worker2, authority2 = _ready_worker(generation=4)

    with pytest.raises(AssignmentConflict) as caught:
        expired.offer_assignment(
            assignment_id="assignment-002",
            worker=worker2,
            worker_authority=authority2,
            spec_digest=_digest("changed-execution-spec"),
            offered_at=AT_EXPIRY,
            expires_at=AT_EXPIRY + timedelta(hours=1),
            expected_version=expired.version,
        )

    assert caught.value.reason == "spec_digest_mismatch"
    assert expired.assignment is None


def test_terminal_assignment_cannot_be_referenced_by_attempt() -> None:
    from qarunner.domain import Attempt, DomainValidationError, RunState

    offered, worker = _offered_initial()
    expired = offered.expire_precommit_assignment(
        assignment_id="assignment-001",
        expiry_key="expiry-001",
        observed_at=AT_EXPIRY,
        expected_version=offered.version,
    )
    attempt = Attempt.create(
        attempt_id="attempt-001",
        run_id=expired.id,
        attempt_no=1,
        fence=1,
        assignment_id="assignment-001",
        worker=worker.ref,
        spec_digest=_digest("execution-spec"),
        start_commit_key="commit-001",
    )

    with pytest.raises(DomainValidationError) as caught:
        replace(
            expired,
            state=RunState.RUNNING,
            current_fence=1,
            current_assignment_id="assignment-001",
            attempts=(attempt,),
        )

    assert caught.value.field == "attempts"
    assert caught.value.reason == "assignment_mismatch"


def test_second_retry_epoch_allows_multiple_closures_before_attempt3() -> None:
    from qarunner.domain import AssignmentState
    from tests.unit.domain.test_unknown_adjudicated_retry import (
        _adjudicated_attempt2_with_retry_intent,
    )

    adjudicated, intent = _adjudicated_attempt2_with_retry_intent()
    retry_queued = adjudicated.queue_adjudicated_retry(
        retry_intent=intent,
        expected_version=adjudicated.version,
    )
    worker3, authority3 = _ready_worker(generation=5)
    offered3 = retry_queued.offer_assignment(
        assignment_id="assignment-003",
        worker=worker3,
        worker_authority=authority3,
        spec_digest=intent.execution_spec_digest,
        offered_at=datetime(2026, 7, 13, 2, tzinfo=UTC),
        expires_at=datetime(2026, 7, 13, 3, tzinfo=UTC),
        expected_version=retry_queued.version,
    )
    expired3 = offered3.expire_precommit_assignment(
        assignment_id="assignment-003",
        expiry_key="shared-closure-key",
        observed_at=datetime(2026, 7, 13, 3, tzinfo=UTC),
        expected_version=offered3.version,
    )
    worker4, authority4 = _ready_worker(generation=6)
    offered4 = expired3.offer_assignment(
        assignment_id="assignment-004",
        worker=worker4,
        worker_authority=authority4,
        spec_digest=intent.execution_spec_digest,
        offered_at=datetime(2026, 7, 13, 3, tzinfo=UTC),
        expires_at=datetime(2026, 7, 13, 4, tzinfo=UTC),
        expected_version=expired3.version,
    )
    released4 = offered4.release_precommit_assignment(
        assignment_id="assignment-004",
        worker=worker4.ref,
        release_key="shared-closure-key",
        observed_at=datetime(2026, 7, 13, 3, 30, tzinfo=UTC),
        expected_version=offered4.version,
    )
    worker5, authority5 = _ready_worker(generation=7)
    offered5 = released4.offer_assignment(
        assignment_id="assignment-005",
        worker=worker5,
        worker_authority=authority5,
        spec_digest=intent.execution_spec_digest,
        offered_at=datetime(2026, 7, 13, 3, 30, tzinfo=UTC),
        expires_at=datetime(2026, 7, 13, 5, tzinfo=UTC),
        expected_version=released4.version,
    )
    claimed5 = offered5.claim_assignment(
        assignment_id="assignment-005",
        worker=worker5.ref,
        observed_at=datetime(2026, 7, 13, 4, tzinfo=UTC),
        expected_version=offered5.version,
    )
    committed = claimed5.commit_start(
        assignment_id="assignment-005",
        worker=worker5.ref,
        start_commit_key="commit-003",
        spec_digest=intent.execution_spec_digest,
        new_attempt_id="attempt-003",
        observed_at=datetime(2026, 7, 13, 4, 1, tzinfo=UTC),
        expected_version=claimed5.version,
    )

    assert tuple(assignment.state for assignment in committed.run.assignments[-3:]) == (
        AssignmentState.EXPIRED_PRESTART,
        AssignmentState.RELEASED_PRESTART,
        AssignmentState.COMMITTED,
    )
    assert committed.run.assignments[-3].closure is not None
    assert committed.run.assignments[-2].closure is not None
    assert (
        committed.run.assignments[-3].closure.idempotency_key
        == committed.run.assignments[-2].closure.idempotency_key
        == "shared-closure-key"
    )
    assert len(committed.run.attempts) == 3
    assert committed.attempt.attempt_no == 3
    assert committed.fence == 3
