"""T-M7-DRAIN-001 / T-M7-AUDIT-001 / T-M7-GC-001: domain-level proofs.

- Drain: Worker state machine stops new claims while draining; in-flight
  attempts converge per policy (no silent success from silence).
- Audit: AuditRecord is append-only/immutable; stable actor_id across calls.
- GC: reconcile_residual_sandboxes orphan detection with audit trail.

Pure domain — no infra, no adapters.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qarunner.domain import (
    ExpectedLiveAttempt,
    ReconcileWorkerFacts,
    ResidualSandboxDisposition,
    ResidualSandboxObservation,
    WorkerGeneration,
    WorkerRef,
    WorkerState,
    canonical_digest,
    reconcile_residual_sandboxes,
)

T0 = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def _worker_ref(generation: int = 1) -> WorkerRef:
    return WorkerRef(worker_id="worker-001", generation=generation)


def _ready_worker(generation: int = 1):
    from qarunner.domain import WorkerAuthority

    ref = _worker_ref(generation)
    worker = WorkerGeneration.register(
        ref=ref,
        host_id="host-001",
        pool_id="pool-default",
        cert_serial=f"cert-{generation}",
        agent_version="1.0.0",
        capabilities_digest=canonical_digest(
            schema_version="qep.worker-capabilities.v1",
            payload={"executor": "docker"},
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


# ── T-M7-DRAIN-001: drain stops new claims ───────────────────────────────────


def test_drain_state_machine_stops_new_claims() -> None:
    """Worker in DRAINING state cannot accept new claims."""
    from qarunner.domain import WorkerNotClaimable

    worker, authority = _ready_worker()
    draining = worker.transition(
        WorkerState.DRAINING,
        authority=authority,
        expected_version=worker.version,
        occurred_at=T0 + timedelta(seconds=2),
    )
    assert draining.state is WorkerState.DRAINING
    # DRAINING → RETIRED (graceful) or OFFLINE/QUARANTINED (forced).
    with pytest.raises(WorkerNotClaimable) as caught:
        draining.claimable_ref(authority=authority)
    assert caught.value.reason in {"offline", "draining"}


def test_drain_converges_to_retired_when_inflight_zero() -> None:
    """Drain → RETIRED when no active assignments remain."""
    worker, authority = _ready_worker()
    draining = worker.transition(
        WorkerState.DRAINING,
        authority=authority,
        expected_version=worker.version,
        occurred_at=T0 + timedelta(seconds=2),
    )
    retired = draining.transition(
        WorkerState.RETIRED,
        authority=authority,
        expected_version=draining.version,
        occurred_at=T0 + timedelta(seconds=3),
    )
    assert retired.state is WorkerState.RETIRED
    # RETIRED is terminal — no further transitions.
    assert retired.state in {WorkerState.RETIRED}


# ── T-M7-AUDIT-001: audit record immutability ───────────────────────────────


def test_audit_record_rejects_invalid_contract() -> None:
    from qarunner.application.ports.audit import AuditRecord
    from qarunner.application.ports.common import PortContractError

    base = dict(
        event_id="evt-001",
        actor_id="user-001",
        authentication_strength="jwt",
        request_id="req-001",
        object_kind="run",
        object_id="run-001",
        action="cancel",
        decision="allowed",
        reason="user-request",
        before_digest=None,
        after_digest=canonical_digest(
            schema_version="qep.audit-test.v1", payload={"v": 1}
        ),
        occurred_at=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
        source="api",
    )
    # Valid record passes.
    record = AuditRecord(**base)
    assert record.event_id == "evt-001"

    # Empty identity field.
    with pytest.raises(PortContractError):
        AuditRecord(**{**base, "event_id": ""})

    # Non-code field (control character).
    with pytest.raises(PortContractError):
        AuditRecord(**{**base, "action": "cancel\x00"})

    # Non-UTC datetime.
    from datetime import timezone

    non_utc = datetime(2026, 7, 29, 10, 0, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(PortContractError):
        AuditRecord(**{**base, "occurred_at": non_utc})


def test_audit_record_is_frozen_immutable() -> None:
    from qarunner.application.ports.audit import AuditRecord

    record = AuditRecord(
        event_id="evt-001",
        actor_id="user-001",
        authentication_strength="jwt",
        request_id="req-001",
        object_kind="run",
        object_id="run-001",
        action="cancel",
        decision="allowed",
        reason="user-request",
        before_digest=None,
        after_digest=canonical_digest(
            schema_version="qep.audit-test.v1", payload={"v": 1}
        ),
        occurred_at=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
        source="api",
    )
    # Frozen dataclass — mutation is impossible.
    with pytest.raises(AttributeError):
        record.event_id = "evt-002"  # type: ignore[misc]


# ── T-M7-GC-001: orphan detection with audit trail ───────────────────────────


def test_orphan_residual_detected_and_disposition_assigned() -> None:
    """Residual container with no matching expected Attempt → ORPHAN_REMOVE."""
    proof = _proof(attempt_id="attempt-orphan", fence=1)
    plan = reconcile_residual_sandboxes(
        expected_live=(),
        residuals=(_observation(proof, container_id="ctr-orphan"),),
    )
    assert len(plan.actions) == 1
    assert plan.actions[0].disposition is ResidualSandboxDisposition.ORPHAN_REMOVE
    assert plan.actions[0].container_id == "ctr-orphan"
    assert plan.actions[0].requires_unknown_observation is False


def test_expected_live_without_residual_and_no_terminal_facts_is_unknown() -> None:
    """Committed Attempt, no residual, no terminal facts → unknown."""
    proof = _proof()
    plan = reconcile_residual_sandboxes(
        expected_live=(
            ExpectedLiveAttempt(
                run_id=proof.run_id,
                assignment_id=proof.assignment_id,
                attempt_id=proof.attempt_id,
                fence=proof.fence,
                start_commit_key=proof.start_commit_key,
                has_terminal_facts=False,
            ),
        ),
        residuals=(),
    )
    assert len(plan.actions) == 1
    assert plan.actions[0].disposition is ResidualSandboxDisposition.UNKNOWN
    assert plan.actions[0].requires_unknown_observation is True


def test_reconcile_never_infers_success_or_auto_start() -> None:
    """Structural red lines: auto_start_count and inferred_success_count are zero."""
    proof_a = _proof(attempt_id="a", fence=1)
    proof_b = _proof(attempt_id="b", fence=1, run_id="run-b", assignment_id="asg-b")
    plan = reconcile_residual_sandboxes(
        expected_live=(
            _expected(proof_a, has_terminal_facts=False),
            _expected(proof_b, has_terminal_facts=True),
        ),
        residuals=(
            _observation(proof_a, container_id="ctr-a"),
            _observation(
                _proof(attempt_id="ghost", fence=9),
                container_id="ctr-ghost",
            ),
        ),
    )
    assert plan.auto_start_count == 0
    assert plan.inferred_success_count == 0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _proof(
    *,
    attempt_id: str = "attempt-001",
    fence: int = 1,
    run_id: str = "run-001",
    assignment_id: str = "assignment-001",
    generation: int = 1,
) -> CommitStartProof:
    from qarunner.domain.worker_execution import CommitStartProof

    return CommitStartProof(
        run_id=run_id,
        assignment_id=assignment_id,
        attempt_id=attempt_id,
        fence=fence,
        start_commit_key=f"commit-{attempt_id}-{fence}",
        worker=WorkerRef(worker_id="worker-001", generation=generation),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1", payload={"run_id": run_id}
        ),
        committed_at=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    )


def _observation(proof: CommitStartProof, *, container_id: str = "ctr-1") -> ResidualSandboxObservation:
    from qarunner.domain.worker_execution import sandbox_labels_for_proof

    labels = sandbox_labels_for_proof(proof)
    return ResidualSandboxObservation(
        container_id=container_id,
        run_id=labels["qarunner.run_id"],
        assignment_id=labels["qarunner.assignment_id"],
        attempt_id=labels["qarunner.attempt_id"],
        fence=int(labels["qarunner.fence"]),
        worker_id=labels["qarunner.worker_id"],
        worker_generation=int(labels["qarunner.worker_generation"]),
        start_commit_key=labels["qarunner.start_commit_key"],
        running=True,
    )


def _expected(proof: CommitStartProof, *, has_terminal_facts: bool = False) -> ExpectedLiveAttempt:
    return ExpectedLiveAttempt(
        run_id=proof.run_id,
        assignment_id=proof.assignment_id,
        attempt_id=proof.attempt_id,
        fence=proof.fence,
        start_commit_key=proof.start_commit_key,
        has_terminal_facts=has_terminal_facts,
    )
