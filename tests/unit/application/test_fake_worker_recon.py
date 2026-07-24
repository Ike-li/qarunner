"""T-M3-RECON-001: Worker restart reconcile on Fake protocol session.

Observable contract (WORKER_PROTOCOL §9 + T-M3-RECON-001):
- after agent restart, new claim is blocked until reconcile completes;
- reconcile consumes only trusted facts (active_assignments / drain_requested);
- control plane does not infer test success from silence / missing facts;
- wrong generation cannot reconcile.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)


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
        payload={"run_id": "run-recon", "profile": "default"},
    )


def test_claim_blocked_until_reconcile_after_agent_restart() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import ReconcileWorkerFacts, WorkerNotClaimable, WorkerState

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
    session.begin_agent_restart()
    assert session.reconcile_pending is True
    with pytest.raises(WorkerNotClaimable) as blocked:
        session.claim(run_id="run-001", assignment_id="assignment-001")
    assert blocked.value.reason in {"reconcile_pending", "offline"}

    recovered = session.reconcile(
        facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False)
    )
    assert recovered.state is WorkerState.READY
    assert session.reconcile_pending is False
    claimed = session.claim(run_id="run-001", assignment_id="assignment-001")
    assert claimed.assignment is not None


def test_reconcile_does_not_infer_test_success_from_silence() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import ReconcileWorkerFacts, RunState

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
    # Agent disappears with no terminal facts.
    session.begin_agent_restart()
    outcome = session.outcome_from_silence(run_id="run-001")
    assert outcome.success is False
    assert outcome.requires_unknown is True
    assert outcome.reason == "silence_no_terminal_facts"
    # Reconcile restores worker readiness only — does not flip Run to succeeded.
    session.reconcile(facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False))
    run = session.get_run("run-001")
    assert run.state is RunState.RUNNING
    assert run.state is not RunState.SUCCEEDED if hasattr(RunState, "SUCCEEDED") else True


def test_reconcile_rejects_wrong_generation() -> None:
    from tests.fakes.greenfield.fake_worker_protocol import FakeWorkerProtocolSession

    from qarunner.domain import (
        ReconcileWorkerFacts,
        WorkerGenerationConflict,
        WorkerRef,
    )

    worker, authority = _ready_worker(generation=1)
    session = FakeWorkerProtocolSession(
        worker=worker,
        worker_authority=authority,
        clock_start=T0 + timedelta(minutes=1),
    )
    session.begin_agent_restart()
    with pytest.raises(WorkerGenerationConflict):
        session.reconcile(
            facts=ReconcileWorkerFacts(active_assignments=0, drain_requested=False),
            authenticated_ref=WorkerRef(worker_id="worker-001", generation=2),
        )
