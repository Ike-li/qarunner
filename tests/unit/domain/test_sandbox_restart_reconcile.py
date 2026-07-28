"""T-M4-RESTART-001: residual sandbox reconcile after Agent/daemon restart.

After a Worker Agent (or host Docker daemon) restarts, residual containers
still carry the M4 attempt/fence labels. The control plane must:

- never invent test success from residual presence or silence;
- never auto-start a second sandbox for the same Attempt/fence;
- classify each residual as keep (still expected live), orphan_remove
  (no matching live control-plane Attempt), or unknown (committed but no
  terminal facts and residual is gone / ambiguous).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qarunner.domain import (
    DomainValidationError,
    ExpectedLiveAttempt,
    ResidualSandboxDisposition,
    ResidualSandboxObservation,
    WorkerRef,
    canonical_digest,
    reconcile_residual_sandboxes,
    sandbox_labels_for_proof,
)
from qarunner.domain.worker_execution import CommitStartProof


def _proof(
    *,
    attempt_id: str = "attempt-001",
    fence: int = 1,
    run_id: str = "run-001",
    assignment_id: str = "assignment-001",
    generation: int = 1,
) -> CommitStartProof:
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
        committed_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )


def _observation(
    proof: CommitStartProof, *, container_id: str = "ctr-1"
) -> ResidualSandboxObservation:
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


# ── Core dispositions ────────────────────────────────────────────────────────


def test_matching_live_residual_is_kept_not_restarted() -> None:
    proof = _proof()
    plan = reconcile_residual_sandboxes(
        expected_live=(_expected(proof),),
        residuals=(_observation(proof),),
    )
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.disposition is ResidualSandboxDisposition.KEEP
    assert action.container_id == "ctr-1"
    assert action.attempt_id == "attempt-001"
    assert action.fence == 1
    assert plan.auto_start_count == 0
    assert plan.inferred_success_count == 0


def test_orphan_residual_with_no_expected_attempt_is_removed() -> None:
    """Daemon left a labeled container whose Attempt is gone from control plane —
    remove the orphan; never treat it as a successful test."""
    proof = _proof(attempt_id="attempt-orphan")
    plan = reconcile_residual_sandboxes(
        expected_live=(),
        residuals=(_observation(proof, container_id="ctr-orphan"),),
    )
    assert len(plan.actions) == 1
    assert plan.actions[0].disposition is ResidualSandboxDisposition.ORPHAN_REMOVE
    assert plan.actions[0].container_id == "ctr-orphan"
    assert plan.auto_start_count == 0
    assert plan.inferred_success_count == 0


def test_expected_live_without_residual_and_without_terminal_facts_is_unknown() -> None:
    """Committed Attempt, no residual container, no terminal facts → unknown,
    never success, never auto-start a replacement sandbox."""
    proof = _proof()
    plan = reconcile_residual_sandboxes(
        expected_live=(_expected(proof, has_terminal_facts=False),),
        residuals=(),
    )
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.disposition is ResidualSandboxDisposition.UNKNOWN
    assert action.container_id is None
    assert action.attempt_id == "attempt-001"
    assert action.requires_unknown_observation is True
    assert plan.auto_start_count == 0
    assert plan.inferred_success_count == 0


def test_expected_live_with_terminal_facts_and_no_residual_is_noop() -> None:
    """Already-terminal Attempt with no residual needs no action (Evidence already
    finalized or unknown already recorded) — still zero auto-starts."""
    proof = _proof()
    plan = reconcile_residual_sandboxes(
        expected_live=(_expected(proof, has_terminal_facts=True),),
        residuals=(),
    )
    assert plan.actions == ()
    assert plan.auto_start_count == 0
    assert plan.inferred_success_count == 0


def test_stale_fence_residual_is_orphan_even_if_attempt_id_matches() -> None:
    """A residual at fence N while control plane expects fence N+1 (retry) is an
    orphan of the prior incarnation — remove it; do not keep or restart it."""
    old = _proof(fence=1)
    new = _proof(fence=2, attempt_id=old.attempt_id)  # same attempt_id, bumped fence
    plan = reconcile_residual_sandboxes(
        expected_live=(_expected(new),),
        residuals=(_observation(old, container_id="ctr-old-fence"),),
    )
    # Old residual → orphan_remove; new expected without residual → unknown.
    dispositions = {a.disposition for a in plan.actions}
    assert ResidualSandboxDisposition.ORPHAN_REMOVE in dispositions
    assert ResidualSandboxDisposition.UNKNOWN in dispositions
    assert plan.auto_start_count == 0
    assert plan.inferred_success_count == 0
    orphan = next(
        a for a in plan.actions if a.disposition is ResidualSandboxDisposition.ORPHAN_REMOVE
    )
    assert orphan.fence == 1
    assert orphan.container_id == "ctr-old-fence"


def test_reconcile_never_reports_inferred_success_or_auto_start() -> None:
    """Structural invariant of every plan: counters that would mean false success
    or duplicate start are always zero (T-M4-RESTART-001 red lines)."""
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
    # Keep a, orphan ghost, noop for b (terminal + no residual).
    by_disp = {}
    for action in plan.actions:
        by_disp.setdefault(action.disposition, []).append(action)
    assert len(by_disp[ResidualSandboxDisposition.KEEP]) == 1
    assert len(by_disp[ResidualSandboxDisposition.ORPHAN_REMOVE]) == 1
    assert ResidualSandboxDisposition.UNKNOWN not in by_disp


# ── Identity / validation ────────────────────────────────────────────────────


def test_duplicate_expected_live_identity_is_rejected() -> None:
    proof = _proof()
    with pytest.raises(DomainValidationError) as caught:
        reconcile_residual_sandboxes(
            expected_live=(_expected(proof), _expected(proof)),
            residuals=(),
        )
    assert caught.value.field == "expected_live"


def test_observation_rejects_invalid_fence_or_empty_ids() -> None:
    with pytest.raises(DomainValidationError):
        ResidualSandboxObservation(
            container_id="c",
            run_id="",
            assignment_id="a",
            attempt_id="t",
            fence=1,
            worker_id="w",
            worker_generation=1,
            start_commit_key="k",
            running=True,
        )
    with pytest.raises(DomainValidationError):
        ResidualSandboxObservation(
            container_id="c",
            run_id="r",
            assignment_id="a",
            attempt_id="t",
            fence=0,
            worker_id="w",
            worker_generation=1,
            start_commit_key="k",
            running=True,
        )


def test_expected_live_rejects_non_bool_terminal_flag() -> None:
    with pytest.raises(DomainValidationError):
        ExpectedLiveAttempt(
            run_id="r",
            assignment_id="a",
            attempt_id="t",
            fence=1,
            start_commit_key="k",
            has_terminal_facts="no",  # type: ignore[arg-type]
        )


def test_observation_rejects_invalid_generation_and_running_flag() -> None:
    with pytest.raises(DomainValidationError):
        ResidualSandboxObservation(
            container_id="c",
            run_id="r",
            assignment_id="a",
            attempt_id="t",
            fence=1,
            worker_id="w",
            worker_generation=0,
            start_commit_key="k",
            running=True,
        )
    with pytest.raises(DomainValidationError):
        ResidualSandboxObservation(
            container_id="c",
            run_id="r",
            assignment_id="a",
            attempt_id="t",
            fence=1,
            worker_id="w",
            worker_generation=1,
            start_commit_key="k",
            running="yes",  # type: ignore[arg-type]
        )


def test_expected_live_rejects_empty_ids_and_invalid_fence() -> None:
    with pytest.raises(DomainValidationError):
        ExpectedLiveAttempt(
            run_id="",
            assignment_id="a",
            attempt_id="t",
            fence=1,
            start_commit_key="k",
            has_terminal_facts=False,
        )
    with pytest.raises(DomainValidationError):
        ExpectedLiveAttempt(
            run_id="r",
            assignment_id="a",
            attempt_id="t",
            fence=0,
            start_commit_key="k",
            has_terminal_facts=False,
        )


def test_reconcile_rejects_untyped_collections() -> None:
    with pytest.raises(DomainValidationError):
        reconcile_residual_sandboxes(expected_live=object(), residuals=())  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        reconcile_residual_sandboxes(expected_live=(), residuals=object())  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        reconcile_residual_sandboxes(expected_live=(object(),), residuals=())  # type: ignore[arg-type]
    with pytest.raises(DomainValidationError):
        reconcile_residual_sandboxes(expected_live=(), residuals=(object(),))  # type: ignore[arg-type]


def test_plan_constructor_forbids_nonzero_red_line_counters() -> None:
    from qarunner.domain import ResidualSandboxReconcilePlan

    with pytest.raises(DomainValidationError):
        ResidualSandboxReconcilePlan(
            actions=(),
            auto_start_count=1,
            inferred_success_count=0,
        )
