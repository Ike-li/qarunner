"""T-M3-UNKNOWN-001: post-commit silence/loss enters unknown without auto-rerun.

Observable contract:
- after commit-start, silence without terminal proof marks Attempt unknown;
- automatic second execution Attempt count remains 0;
- exact unknown observation replay is stable;
- Fake session does not auto-start a second Attempt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 7, 24, 15, 0, tzinfo=UTC)


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
        payload={"run_id": "run-unknown", "profile": "default"},
    )


def _session():
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    worker, authority = _ready_worker()
    return FakeWorkerProtocolSession(
        worker=worker,
        worker_authority=authority,
        clock_start=T0 + timedelta(minutes=1),
    )


def test_post_commit_silence_marks_unknown_without_second_attempt() -> None:
    from qarunner.domain import AttemptState

    session = _session()
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
    outcome = session.outcome_from_silence(run_id="run-001")
    assert outcome.requires_unknown is True
    assert outcome.success is False

    unknown_run = session.mark_unknown_from_silence(
        run_id="run-001",
        observation_id="unknown-001",
    )
    attempt = next(item for item in unknown_run.attempts if item.id == "attempt-001")
    assert attempt.state is AttemptState.ATTEMPT_UNKNOWN
    assert attempt.unknown_observation is not None
    assert attempt.unknown_observation.reason.value == "worker_lost_after_commit"
    # No automatic second Attempt.
    assert len(unknown_run.attempts) == 1
    assert session.automatic_second_execution_attempt_total == 0


def test_unknown_from_silence_exact_replay_is_stable() -> None:
    session = _session()
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
    first = session.mark_unknown_from_silence(
        run_id="run-001",
        observation_id="unknown-001",
    )
    second = session.mark_unknown_from_silence(
        run_id="run-001",
        observation_id="unknown-001",
    )
    assert second.version == first.version
    assert len(second.attempts) == 1
    assert session.automatic_second_execution_attempt_total == 0


def test_cannot_auto_commit_start_second_attempt_after_unknown() -> None:
    from qarunner.domain import AssignmentConflict

    session = _session()
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
    session.mark_unknown_from_silence(run_id="run-001", observation_id="unknown-001")
    # Auto second start must fail closed — no new claimable path without policy retry.
    with pytest.raises((AssignmentConflict, Exception)):
        session.commit_start(
            run_id="run-001",
            assignment_id="assignment-001",
            start_commit_key="commit-key-2",
            new_attempt_id="attempt-002",
        )
    run = session.get_run("run-001")
    assert len(run.attempts) == 1
    assert session.automatic_second_execution_attempt_total == 0
