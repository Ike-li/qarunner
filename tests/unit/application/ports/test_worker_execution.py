"""T-M4 WorkerExecutor port + Fake: execute only after commit-start proof.

M4 single-ECS scope. Real Docker isolation is proven under -m docker; this Fake
records admitted executions and refuses missing proofs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

T0 = datetime(2026, 7, 24, 16, 5, tzinfo=UTC)


def _proof():
    from qarunner.domain import CommitStartProof, WorkerRef, canonical_digest

    return CommitStartProof(
        run_id="run-001",
        assignment_id="assignment-001",
        attempt_id="attempt-001",
        fence=1,
        start_commit_key="commit-key-1",
        worker=WorkerRef(worker_id="worker-001", generation=1),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"label": "spec"},
        ),
        committed_at=T0,
    )


def test_fake_executor_satisfies_port() -> None:
    from tests.fakes.greenfield.worker_executor import InMemoryWorkerExecutor

    from qarunner.application.ports.worker_execution import WorkerExecutor

    assert isinstance(InMemoryWorkerExecutor(), WorkerExecutor)


@pytest.mark.asyncio
async def test_fake_executor_requires_commit_start_proof() -> None:
    from tests.fakes.greenfield.worker_executor import InMemoryWorkerExecutor

    from qarunner.domain import ExecutionAdmissionError

    executor = InMemoryWorkerExecutor()
    with pytest.raises(ExecutionAdmissionError):
        await executor.execute_pytest_shard(
            proof=None,  # type: ignore[arg-type]
            cmd=["python", "-m", "pytest", "-q"],
            cwd="/tmp/suite",
        )


@pytest.mark.asyncio
async def test_fake_executor_records_admitted_execution_labels() -> None:
    from tests.fakes.greenfield.worker_executor import InMemoryWorkerExecutor

    executor = InMemoryWorkerExecutor()
    proof = _proof()
    result = await executor.execute_pytest_shard(
        proof=proof,
        cmd=["python", "-m", "pytest", "-q"],
        cwd="/tmp/suite",
        timeout=30,
    )
    assert result.exit_code == 0
    assert len(executor.executions) == 1
    recorded = executor.executions[0]
    assert recorded.labels["qarunner.attempt_id"] == "attempt-001"
    assert recorded.labels["qarunner.fence"] == "1"
    assert recorded.cmd[:3] == ["python", "-m", "pytest"]
