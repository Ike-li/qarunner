"""M4-DOCKER-LABELS: DockerWorkerExecutor requires proof and stamps labels."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from qarunner.adapters.docker_worker_executor import DockerWorkerExecutor
from qarunner.application.ports.worker_execution import WorkerExecutor
from qarunner.domain import (
    CommitStartProof,
    ExecutionAdmissionError,
    WorkerRef,
    canonical_digest,
)
from qarunner.models import ProcessResult

T0 = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)


def _proof() -> CommitStartProof:
    return CommitStartProof(
        run_id="run-001",
        assignment_id="assignment-001",
        attempt_id="attempt-001",
        fence=2,
        start_commit_key="commit-key-1",
        worker=WorkerRef(worker_id="worker-001", generation=1),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"label": "spec"},
        ),
        committed_at=T0,
    )


def test_docker_worker_executor_satisfies_port() -> None:
    assert isinstance(DockerWorkerExecutor(runner=MagicMock()), WorkerExecutor)


@pytest.mark.asyncio
async def test_docker_worker_executor_refuses_missing_proof() -> None:
    runner = MagicMock()
    runner.run = AsyncMock()
    executor = DockerWorkerExecutor(runner=runner)
    with pytest.raises(ExecutionAdmissionError):
        await executor.execute_pytest_shard(
            proof=None,  # type: ignore[arg-type]
            cmd=["python", "-m", "pytest", "-q"],
            cwd="/tmp/suite",
        )
    runner.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_worker_executor_passes_attempt_fence_labels() -> None:
    runner = MagicMock()
    runner.run = AsyncMock(
        return_value=ProcessResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=12,
            timed_out=False,
        )
    )
    executor = DockerWorkerExecutor(runner=runner)
    proof = _proof()
    result = await executor.execute_pytest_shard(
        proof=proof,
        cmd=["python", "-m", "pytest", "-q"],
        cwd="/tmp/suite",
        timeout=30,
        env={"FOO": "bar"},
    )
    assert result.exit_code == 0
    assert result.labels["qarunner.attempt_id"] == "attempt-001"
    assert result.labels["qarunner.fence"] == "2"
    kwargs = runner.run.await_args.kwargs
    assert kwargs["cmd"] == ["python", "-m", "pytest", "-q"]
    assert kwargs["cwd"] == "/tmp/suite"
    assert kwargs["timeout"] == 30
    assert kwargs["env"] == {"FOO": "bar"}
    assert kwargs["labels"]["qarunner.run_id"] == "run-001"
    assert kwargs["labels"]["qarunner.assignment_id"] == "assignment-001"
    assert kwargs["labels"]["qarunner.attempt_id"] == "attempt-001"
    assert kwargs["labels"]["qarunner.fence"] == "2"
    assert kwargs["labels"]["qarunner.worker_id"] == "worker-001"
    assert kwargs["labels"]["qarunner.worker_generation"] == "1"
