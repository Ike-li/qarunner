"""T-M3 Fake Worker protocol session (claim/commit/renew/event/generation).

Composes existing Run/Assignment/Attempt domain contracts into a deterministic
in-memory protocol model. Does not call Docker or network. Proves:
- claim only when Worker is claimable and assignment offered;
- commit-start replay returns same attempt/fence;
- renew lease monotonicity;
- event seq idempotency / conflict;
- generation rotation rejects old worker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _ready_worker(*, generation: int = 1):
    from qarunner.domain import (
        WorkerAuthority,
        WorkerGeneration,
        WorkerRef,
        WorkerState,
        canonical_digest,
    )

    ref = WorkerRef(worker_id="worker-001", generation=generation)
    worker = WorkerGeneration.register(
        ref=ref,
        host_id="host-001",
        pool_id="pool-default",
        cert_serial=f"cert-{generation}",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.worker-capabilities.v1",
            payload={"executor": "fake"},
        ),
        registered_at=T0,
    )
    authority = WorkerAuthority(current_ref=ref)
    ready = worker.transition(
        WorkerState.READY,
        authority=authority,
        expected_version=0,
        occurred_at=T0 + timedelta(seconds=1),
    )
    return ready, authority


def _spec():
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"run_id": "run-001", "profile": "default"},
    )


def test_fake_worker_claim_commit_renew_event_happy_path() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    worker, authority = _ready_worker()
    session = FakeWorkerProtocolSession(
        worker=worker,
        worker_authority=authority,
        clock_start=T0 + timedelta(minutes=1),
    )
    run = session.seed_offered_run(
        run_id="run-001",
        assignment_id="assignment-001",
        spec_digest=_spec(),
        expires_at=T0 + timedelta(hours=1),
    )
    claimed = session.claim(run_id=run.id, assignment_id="assignment-001")
    assert claimed.assignment is not None
    assert claimed.assignment.state.value == "claimed"

    commit = session.commit_start(
        run_id=run.id,
        assignment_id="assignment-001",
        start_commit_key="commit-key-1",
        new_attempt_id="attempt-001",
    )
    assert commit.replayed is False
    assert commit.fence == 1
    assert commit.attempt.id == "attempt-001"

    replay = session.commit_start(
        run_id=run.id,
        assignment_id="assignment-001",
        start_commit_key="commit-key-1",
        new_attempt_id="attempt-001",
    )
    assert replay.replayed is True
    assert replay.fence == commit.fence
    assert replay.attempt.id == commit.attempt.id

    lease = session.renew(
        assignment_id="assignment-001",
        request_lease_version=1,
        ttl=timedelta(seconds=30),
    )
    assert lease.lease_version == 2

    accepted = session.record_event(
        run_id=run.id,
        attempt_id="attempt-001",
        event_id="evt-1",
        event_seq=1,
        event_type="phase",
        payload_label="running",
    )
    assert accepted.replayed is False
    duplicate = session.record_event(
        run_id=run.id,
        attempt_id="attempt-001",
        event_id="evt-1",
        event_seq=1,
        event_type="phase",
        payload_label="running",
    )
    assert duplicate.replayed is True


def test_fake_worker_rejects_claim_when_not_claimable() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import WorkerNotClaimable, WorkerState

    worker, authority = _ready_worker()
    session = FakeWorkerProtocolSession(
        worker=worker,
        worker_authority=authority,
        clock_start=T0 + timedelta(minutes=1),
    )
    session.seed_offered_run(
        run_id="run-001",
        assignment_id="assignment-001",
        spec_digest=_spec(),
        expires_at=T0 + timedelta(hours=1),
    )
    offline = worker.transition(
        WorkerState.OFFLINE,
        authority=authority,
        expected_version=worker.version,
        occurred_at=T0 + timedelta(seconds=2),
    )
    session.rotate_generation(worker=offline, worker_authority=authority)
    with pytest.raises(WorkerNotClaimable):
        session.claim(run_id="run-001", assignment_id="assignment-001")


def test_fake_worker_event_conflict_on_same_seq_different_digest() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import EventConflict

    worker, authority = _ready_worker()
    session = FakeWorkerProtocolSession(
        worker=worker,
        worker_authority=authority,
        clock_start=T0 + timedelta(minutes=1),
    )
    session.seed_offered_run(
        run_id="run-001",
        assignment_id="assignment-001",
        spec_digest=_spec(),
        expires_at=T0 + timedelta(hours=1),
    )
    session.claim(run_id="run-001", assignment_id="assignment-001")
    session.commit_start(
        run_id="run-001",
        assignment_id="assignment-001",
        start_commit_key="commit-key-1",
        new_attempt_id="attempt-001",
    )
    session.record_event(
        run_id="run-001",
        attempt_id="attempt-001",
        event_id="evt-1",
        event_seq=1,
        event_type="phase",
        payload_label="running",
    )
    with pytest.raises(EventConflict):
        session.record_event(
            run_id="run-001",
            attempt_id="attempt-001",
            event_id="evt-1",
            event_seq=1,
            event_type="phase",
            payload_label="different",
        )


def test_fake_worker_generation_rotation_rejects_old_worker() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import WorkerGenerationConflict

    worker_v1, authority_v1 = _ready_worker(generation=1)
    session = FakeWorkerProtocolSession(
        worker=worker_v1,
        worker_authority=authority_v1,
        clock_start=T0 + timedelta(minutes=1),
    )
    session.seed_offered_run(
        run_id="run-001",
        assignment_id="assignment-001",
        spec_digest=_spec(),
        expires_at=T0 + timedelta(hours=1),
    )
    session.claim(run_id="run-001", assignment_id="assignment-001")
    session.commit_start(
        run_id="run-001",
        assignment_id="assignment-001",
        start_commit_key="commit-key-1",
        new_attempt_id="attempt-001",
    )
    # Rotate generation on the session (control plane activated new identity).
    # Renew uses the session's current generation (v2) against a lease bound to v1.
    worker_v2, authority_v2 = _ready_worker(generation=2)
    session.rotate_generation(worker=worker_v2, worker_authority=authority_v2)
    with pytest.raises(WorkerGenerationConflict):
        session.renew(
            assignment_id="assignment-001",
            request_lease_version=1,
            ttl=timedelta(seconds=30),
        )


def test_fake_worker_does_not_import_runtime() -> None:
    from pathlib import Path

    source = Path("tests/fakes/greenfield/fake_worker_protocol.py").read_text(encoding="utf-8")
    forbidden = ("import docker", "subprocess", "asyncpg", "importlib")
    for token in forbidden:
        assert token not in source, token


def test_fake_worker_event_sequence_gap_preserves_history() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import EventSequenceGap

    worker, authority = _ready_worker()
    session = FakeWorkerProtocolSession(
        worker=worker,
        worker_authority=authority,
        clock_start=T0 + timedelta(minutes=1),
    )
    session.seed_offered_run(
        run_id="run-001",
        assignment_id="assignment-001",
        spec_digest=_spec(),
        expires_at=T0 + timedelta(hours=1),
    )
    session.claim(run_id="run-001", assignment_id="assignment-001")
    session.commit_start(
        run_id="run-001",
        assignment_id="assignment-001",
        start_commit_key="commit-key-1",
        new_attempt_id="attempt-001",
    )
    session.record_event(
        run_id="run-001",
        attempt_id="attempt-001",
        event_id="evt-1",
        event_seq=1,
        event_type="phase",
        payload_label="running",
    )
    with pytest.raises(EventSequenceGap) as caught:
        session.record_event(
            run_id="run-001",
            attempt_id="attempt-001",
            event_id="evt-3",
            event_seq=3,
            event_type="phase",
            payload_label="uploading",
        )
    assert caught.value.expected_next == 2
    assert caught.value.received_seq == 3
