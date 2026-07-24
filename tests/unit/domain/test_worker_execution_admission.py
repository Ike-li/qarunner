"""T-M4-BOUNDARY-001 / execution admission: no sandbox without commit-start.

Observable M4 control-plane boundary (single-ECS):
- Worker execution requires a durable CommitStartProof (attempt/fence/assignment);
- incomplete or zero fence proofs are rejected;
- control plane cannot treat a bare command as executable work.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

T0 = datetime(2026, 7, 24, 16, 0, tzinfo=UTC)


def _worker():
    from qarunner.domain import WorkerRef

    return WorkerRef(worker_id="worker-001", generation=1)


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.execution-spec.v1",
        payload={"label": label},
    )


def test_commit_start_proof_is_required_for_execution() -> None:
    from qarunner.domain import (
        ExecutionAdmissionError,
        require_execution_admission,
    )

    with pytest.raises(ExecutionAdmissionError) as caught:
        require_execution_admission(proof=None)  # type: ignore[arg-type]
    assert caught.value.reason == "commit_start_required"


def test_complete_commit_start_proof_admits_execution() -> None:
    from qarunner.domain import (
        CommitStartProof,
        ExecutionSandboxProfile,
        require_execution_admission,
    )

    proof = CommitStartProof(
        run_id="run-001",
        assignment_id="assignment-001",
        attempt_id="attempt-001",
        fence=1,
        start_commit_key="commit-key-1",
        worker=_worker(),
        spec_digest=_digest("spec"),
        committed_at=T0,
    )
    admitted = require_execution_admission(proof=proof)
    assert admitted.attempt_id == "attempt-001"
    assert admitted.fence == 1
    profile = ExecutionSandboxProfile.m4_pytest_default()
    assert profile.network_mode == "none"
    assert profile.read_only_root is True
    assert profile.cap_drop == ("ALL",)
    assert profile.no_new_privileges is True


def test_zero_or_missing_fence_is_rejected() -> None:

    from qarunner.domain import (
        CommitStartProof,
        ExecutionAdmissionError,
        require_execution_admission,
    )

    # Bypass dataclass validation to exercise admission gate on fence.
    proof = CommitStartProof(
        run_id="run-001",
        assignment_id="assignment-001",
        attempt_id="attempt-001",
        fence=1,
        start_commit_key="commit-key-1",
        worker=_worker(),
        spec_digest=_digest("spec"),
        committed_at=T0,
    )
    object.__setattr__(proof, "fence", 0)
    with pytest.raises(ExecutionAdmissionError) as caught:
        require_execution_admission(proof=proof)
    assert caught.value.reason == "invalid_fence"


def test_sandbox_labels_bind_attempt_and_fence() -> None:
    from qarunner.domain import CommitStartProof, sandbox_labels_for_proof

    proof = CommitStartProof(
        run_id="run-001",
        assignment_id="assignment-001",
        attempt_id="attempt-001",
        fence=3,
        start_commit_key="commit-key-1",
        worker=_worker(),
        spec_digest=_digest("spec"),
        committed_at=T0,
    )
    labels = sandbox_labels_for_proof(proof)
    assert labels["qarunner.run_id"] == "run-001"
    assert labels["qarunner.assignment_id"] == "assignment-001"
    assert labels["qarunner.attempt_id"] == "attempt-001"
    assert labels["qarunner.fence"] == "3"
    assert labels["qarunner.worker_id"] == "worker-001"
    assert labels["qarunner.worker_generation"] == "1"


def test_commit_start_proof_rejects_invalid_identity_fields() -> None:
    from qarunner.domain import CommitStartProof, DomainValidationError

    with pytest.raises(DomainValidationError):
        CommitStartProof(
            run_id="",
            assignment_id="assignment-001",
            attempt_id="attempt-001",
            fence=1,
            start_commit_key="commit-key-1",
            worker=_worker(),
            spec_digest=_digest("spec"),
            committed_at=T0,
        )
    with pytest.raises(DomainValidationError):
        CommitStartProof(
            run_id="run-001",
            assignment_id="assignment-001",
            attempt_id="attempt-001",
            fence=1,
            start_commit_key="commit-key-1",
            worker=object(),  # type: ignore[arg-type]
            spec_digest=_digest("spec"),
            committed_at=T0,
        )
    with pytest.raises(DomainValidationError):
        CommitStartProof(
            run_id="run-001",
            assignment_id="assignment-001",
            attempt_id="attempt-001",
            fence=1,
            start_commit_key="commit-key-1",
            worker=_worker(),
            spec_digest=_digest("spec"),
            committed_at=T0.replace(tzinfo=None),
        )


def test_commit_start_proof_rejects_non_digest_and_invalid_fence_bool() -> None:
    from qarunner.domain import CommitStartProof, DomainValidationError

    with pytest.raises(DomainValidationError):
        CommitStartProof(
            run_id="run-001",
            assignment_id="assignment-001",
            attempt_id="attempt-001",
            fence=1,
            start_commit_key="commit-key-1",
            worker=_worker(),
            spec_digest="not-a-digest",  # type: ignore[arg-type]
            committed_at=T0,
        )
    with pytest.raises(DomainValidationError):
        CommitStartProof(
            run_id="run-001",
            assignment_id="assignment-001",
            attempt_id="attempt-001",
            fence=True,  # type: ignore[arg-type]
            start_commit_key="commit-key-1",
            worker=_worker(),
            spec_digest=_digest("spec"),
            committed_at=T0,
        )
    with pytest.raises(DomainValidationError):
        CommitStartProof(
            run_id="run-001",
            assignment_id="assignment-001",
            attempt_id="attempt-001",
            fence=1,
            start_commit_key="commit-key-1",
            worker=_worker(),
            spec_digest=_digest("spec"),
            committed_at="now",  # type: ignore[arg-type]
        )
