"""T-M0-COMMIT-001: commit-start creates one durable Attempt and fence."""

import pytest


def _claimed_run():
    from qarunner.domain import Run, RunState, WorkerRef, canonical_digest

    spec_digest = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001", "profile_id": "profile-001"},
    )
    worker = WorkerRef(worker_id="worker-001", generation=3)
    planned = Run.create(run_id="run-001")
    queued = planned.transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=worker,
        spec_digest=spec_digest,
        expected_version=1,
    )
    claimed = offered.claim_assignment(
        assignment_id="assignment-001",
        worker=worker,
        expected_version=2,
    )
    return claimed, worker, spec_digest


def test_claimed_assignment_commit_creates_first_attempt_and_fence() -> None:
    """A Worker receives durable execution identity before it may start work."""
    from qarunner.domain import RunState

    claimed, worker, spec_digest = _claimed_run()

    committed = claimed.commit_start(
        assignment_id="assignment-001",
        worker=worker,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="attempt-001",
        expected_version=3,
    )

    assert committed.replayed is False
    assert committed.fence == 1
    assert committed.attempt.id == "attempt-001"
    assert committed.attempt.run_id == "run-001"
    assert committed.attempt.attempt_no == 1
    assert committed.attempt.fence == 1
    assert committed.attempt.worker == worker
    assert committed.attempt.spec_digest == spec_digest
    assert committed.run.state == RunState.RUNNING
    assert committed.run.current_fence == 1
    assert committed.run.attempts == (committed.attempt,)
    assert committed.run.version == 4
    assert claimed.state == RunState.ASSIGNED
    assert claimed.current_fence == 0
    assert claimed.attempts == ()
    assert claimed.version == 3


def test_commit_response_loss_replays_the_same_attempt_and_fence() -> None:
    """An exact commit replay wins before CAS and never allocates a new identity."""
    claimed, worker, spec_digest = _claimed_run()
    first = claimed.commit_start(
        assignment_id="assignment-001",
        worker=worker,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="attempt-001",
        expected_version=3,
    )

    replay = first.run.commit_start(
        assignment_id="assignment-001",
        worker=worker,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="must-not-be-used",
        expected_version=3,
    )

    assert replay.replayed is True
    assert replay.attempt == first.attempt
    assert replay.fence == first.fence == 1
    assert replay.run == first.run
    assert len(replay.run.attempts) == 1
    assert replay.run.version == 4


def test_commit_key_reuse_with_changed_spec_is_rejected() -> None:
    """A Worker cannot change commit content while retaining its idempotency key."""
    from qarunner.domain import IdempotencyConflict, canonical_digest

    claimed, worker, spec_digest = _claimed_run()
    first = claimed.commit_start(
        assignment_id="assignment-001",
        worker=worker,
        start_commit_key="commit-001",
        spec_digest=spec_digest,
        new_attempt_id="attempt-001",
        expected_version=3,
    )
    changed_spec = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001", "profile_id": "profile-002"},
    )

    with pytest.raises(IdempotencyConflict) as caught:
        first.run.commit_start(
            assignment_id="assignment-001",
            worker=worker,
            start_commit_key="commit-001",
            spec_digest=changed_spec,
            new_attempt_id="attempt-002",
            expected_version=4,
        )

    assert caught.value.code == "idempotency_conflict"
    assert caught.value.scope == "run:run-001:start-commit"
    assert caught.value.key == "commit-001"
    assert caught.value.stored_digest != caught.value.received_digest
    assert first.run.current_fence == 1
    assert len(first.run.attempts) == 1


def test_assignment_offer_requires_a_queued_run() -> None:
    """Assignment creation cannot skip the Run queue transition."""
    from qarunner.domain import AssignmentConflict, Run, WorkerRef, canonical_digest

    planned = Run.create(run_id="run-001")
    worker = WorkerRef(worker_id="worker-001", generation=3)
    spec_digest = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001"},
    )

    with pytest.raises(AssignmentConflict) as caught:
        planned.offer_assignment(
            assignment_id="assignment-001",
            worker=worker,
            spec_digest=spec_digest,
            expected_version=0,
        )

    assert caught.value.code == "assignment_conflict"
    assert caught.value.run_id == "run-001"
    assert caught.value.assignment_id == "assignment-001"
    assert caught.value.reason == "run_not_queued"
    assert planned.assignment is None
    assert planned.version == 0


def test_assignment_claim_rejects_a_different_worker_generation() -> None:
    """Only the exact Worker generation named in the offer may claim it."""
    from qarunner.domain import (
        AssignmentConflict,
        Run,
        RunState,
        WorkerRef,
        canonical_digest,
    )

    bound_worker = WorkerRef(worker_id="worker-001", generation=3)
    wrong_generation = WorkerRef(worker_id="worker-001", generation=4)
    spec_digest = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001"},
    )
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=bound_worker,
        spec_digest=spec_digest,
        expected_version=1,
    )

    with pytest.raises(AssignmentConflict) as caught:
        offered.claim_assignment(
            assignment_id="assignment-001",
            worker=wrong_generation,
            expected_version=2,
        )

    assert caught.value.reason == "offer_mismatch"
    assert offered.assignment is not None
    assert offered.assignment.worker == bound_worker
    assert offered.version == 2


def test_commit_start_requires_the_exact_claimed_assignment() -> None:
    """An offered-but-unclaimed reservation cannot create execution identity."""
    from qarunner.domain import (
        AssignmentConflict,
        Run,
        RunState,
        WorkerRef,
        canonical_digest,
    )

    worker = WorkerRef(worker_id="worker-001", generation=3)
    spec_digest = canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001"},
    )
    queued = Run.create(run_id="run-001").transition(RunState.QUEUED, expected_version=0)
    offered = queued.offer_assignment(
        assignment_id="assignment-001",
        worker=worker,
        spec_digest=spec_digest,
        expected_version=1,
    )

    with pytest.raises(AssignmentConflict) as caught:
        offered.commit_start(
            assignment_id="assignment-001",
            worker=worker,
            start_commit_key="commit-001",
            spec_digest=spec_digest,
            new_attempt_id="attempt-001",
            expected_version=2,
        )

    assert caught.value.reason == "assignment_not_claimed"
    assert offered.current_fence == 0
    assert offered.attempts == ()
    assert offered.version == 2
