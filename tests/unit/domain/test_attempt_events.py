"""T-M0-FENCE-001: Attempt events are fenced to one Worker generation."""

import pytest


def _attempt_at_fence_two():
    from qarunner.domain import Attempt, WorkerRef, canonical_digest

    return Attempt.create(
        attempt_id="attempt-002",
        run_id="run-001",
        attempt_no=2,
        fence=2,
        assignment_id="assignment-002",
        worker=WorkerRef(worker_id="worker-001", generation=3),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"run_id": "run-001"},
        ),
        start_commit_key="commit-002",
    )


def test_old_fence_event_is_rejected_without_mutation() -> None:
    """Late facts from a prior Attempt cannot advance the current Attempt."""
    from qarunner.domain import AttemptEvent, StaleFence, WorkerRef, canonical_digest

    attempt = _attempt_at_fence_two()
    event = AttemptEvent(
        event_id="event-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )

    with pytest.raises(StaleFence) as caught:
        attempt.record_event(
            event,
            worker=WorkerRef(worker_id="worker-001", generation=3),
            fence=1,
            expected_version=0,
        )

    assert caught.value.code == "stale_fence"
    assert caught.value.attempt_id == "attempt-002"
    assert caught.value.current_fence == 2
    assert caught.value.received_fence == 1
    assert attempt.events == ()
    assert attempt.version == 0


def test_old_worker_generation_event_is_rejected_without_mutation() -> None:
    """Rebuilt Worker identity cannot be impersonated by its retired generation."""
    from qarunner.domain import AttemptEvent, StaleGeneration, WorkerRef, canonical_digest

    attempt = _attempt_at_fence_two()
    event = AttemptEvent(
        event_id="event-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )

    with pytest.raises(StaleGeneration) as caught:
        attempt.record_event(
            event,
            worker=WorkerRef(worker_id="worker-001", generation=2),
            fence=2,
            expected_version=0,
        )

    assert caught.value.code == "stale_generation"
    assert caught.value.attempt_id == "attempt-002"
    assert caught.value.current_worker == WorkerRef(worker_id="worker-001", generation=3)
    assert caught.value.received_worker == WorkerRef(worker_id="worker-001", generation=2)
    assert attempt.events == ()
    assert attempt.version == 0


def test_current_worker_and_fence_append_an_immutable_event() -> None:
    """A valid event advances only the returned Attempt version."""
    from qarunner.domain import AttemptEvent, WorkerRef, canonical_digest

    attempt = _attempt_at_fence_two()
    event = AttemptEvent(
        event_id="event-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )

    updated = attempt.record_event(
        event,
        worker=WorkerRef(worker_id="worker-001", generation=3),
        fence=2,
        expected_version=0,
    )

    assert updated.events == (event,)
    assert updated.version == 1
    assert attempt.events == ()
    assert attempt.version == 0


def test_exact_event_replay_wins_before_cas_without_duplication() -> None:
    """Response loss can replay the same event ID/seq/digest with a stale version."""
    from qarunner.domain import AttemptEvent, WorkerRef, canonical_digest

    attempt = _attempt_at_fence_two()
    worker = WorkerRef(worker_id="worker-001", generation=3)
    event = AttemptEvent(
        event_id="event-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )
    updated = attempt.record_event(event, worker=worker, fence=2, expected_version=0)

    replay = updated.record_event(event, worker=worker, fence=2, expected_version=0)

    assert replay == updated
    assert replay.events == (event,)
    assert replay.version == 1


def test_event_id_or_sequence_reuse_with_different_content_is_rejected() -> None:
    """Conflicting event history cannot be overwritten or bypassed with the same seq."""
    from qarunner.domain import AttemptEvent, EventConflict, WorkerRef, canonical_digest

    attempt = _attempt_at_fence_two()
    worker = WorkerRef(worker_id="worker-001", generation=3)
    original = AttemptEvent(
        event_id="event-001",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "rootless-docker"},
        ),
    )
    updated = attempt.record_event(original, worker=worker, fence=2, expected_version=0)
    conflict = AttemptEvent(
        event_id="event-002",
        event_seq=1,
        event_type="sandbox_create_started",
        payload_digest=canonical_digest(
            schema_version="qep.attempt-event-payload.v1",
            payload={"runtime": "different"},
        ),
    )

    with pytest.raises(EventConflict) as caught:
        updated.record_event(conflict, worker=worker, fence=2, expected_version=1)

    assert caught.value.code == "event_conflict"
    assert caught.value.attempt_id == "attempt-002"
    assert caught.value.stored_event == original
    assert caught.value.received_event == conflict
    assert updated.events == (original,)
    assert updated.version == 1
