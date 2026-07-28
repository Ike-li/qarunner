"""M4-DOCKER-LABELS / M4-ISOLATE: DockerWorkerExecutor requires proof, stamps
labels, and jails each Attempt's workspace so a consecutive Attempt can never
read a prior Attempt's workspace (T-M4-ISOLATE-001)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
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
from qarunner.errors import RunnerError
from qarunner.models import ProcessResult

T0 = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)


def _proof(*, attempt_id: str = "attempt-001", fence: int = 2) -> CommitStartProof:
    return CommitStartProof(
        run_id="run-001",
        assignment_id="assignment-001",
        attempt_id=attempt_id,
        fence=fence,
        start_commit_key="commit-key-1",
        worker=WorkerRef(worker_id="worker-001", generation=1),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"label": "spec"},
        ),
        committed_at=T0,
    )


def _ok_result() -> ProcessResult:
    return ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=12, timed_out=False)


def _write_suite(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "test_sample.py").write_text("def test_x():\n    assert True\n")
    return root


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
async def test_docker_worker_executor_passes_attempt_fence_labels(tmp_path: Path) -> None:
    source = _write_suite(tmp_path / "suite")
    runner = MagicMock()
    runner.run = AsyncMock(return_value=_ok_result())
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(tmp_path / "workspaces"))
    proof = _proof()
    result = await executor.execute_pytest_shard(
        proof=proof,
        cmd=["python", "-m", "pytest", "-q"],
        cwd=str(source),
        timeout=30,
        env={"FOO": "bar"},
    )
    assert result.exit_code == 0
    assert result.labels["qarunner.attempt_id"] == "attempt-001"
    assert result.labels["qarunner.fence"] == "2"
    kwargs = runner.run.await_args.kwargs
    assert kwargs["cmd"] == ["python", "-m", "pytest", "-q"]
    assert kwargs["timeout"] == 30
    assert kwargs["env"] == {"FOO": "bar"}
    assert kwargs["labels"]["qarunner.run_id"] == "run-001"
    assert kwargs["labels"]["qarunner.assignment_id"] == "assignment-001"
    assert kwargs["labels"]["qarunner.attempt_id"] == "attempt-001"
    assert kwargs["labels"]["qarunner.fence"] == "2"
    assert kwargs["labels"]["qarunner.worker_id"] == "worker-001"
    assert kwargs["labels"]["qarunner.worker_generation"] == "1"


@pytest.mark.asyncio
async def test_docker_worker_executor_jails_source_into_attempt_scoped_workspace(
    tmp_path: Path,
) -> None:
    """T-M4-ISOLATE-001: the sandbox runs against a copy under a workspace path
    bound to run/assignment/attempt/fence — never the caller's shared source."""
    source = _write_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    runner = MagicMock()
    runner.run = AsyncMock(return_value=_ok_result())
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(workspace_root))

    await executor.execute_pytest_shard(
        proof=_proof(),
        cmd=["python", "-m", "pytest", "-q"],
        cwd=str(source),
    )

    jailed_cwd = runner.run.await_args.kwargs["cwd"]
    assert jailed_cwd != str(source)
    assert jailed_cwd == str(workspace_root / "run-001/assignment-001/attempt-001/2")


@pytest.mark.asyncio
async def test_docker_worker_executor_cleans_up_jail_after_execution(tmp_path: Path) -> None:
    source = _write_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    runner = MagicMock()
    runner.run = AsyncMock(return_value=_ok_result())
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(workspace_root))

    await executor.execute_pytest_shard(
        proof=_proof(),
        cmd=["python", "-m", "pytest", "-q"],
        cwd=str(source),
    )

    jailed_cwd = Path(runner.run.await_args.kwargs["cwd"])
    assert not jailed_cwd.exists()
    # The shared source suite itself is untouched.
    assert source.exists()


@pytest.mark.asyncio
async def test_docker_worker_executor_cleans_up_jail_even_on_runner_failure(
    tmp_path: Path,
) -> None:
    source = _write_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    expected_jail = workspace_root / "run-001/assignment-001/attempt-001/2"
    runner = MagicMock()
    runner.run = AsyncMock(side_effect=RuntimeError("docker daemon unreachable"))
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(workspace_root))

    with pytest.raises(RuntimeError, match="docker daemon unreachable"):
        await executor.execute_pytest_shard(
            proof=_proof(),
            cmd=["python", "-m", "pytest", "-q"],
            cwd=str(source),
        )

    assert not expected_jail.exists()


@pytest.mark.asyncio
async def test_docker_worker_executor_wraps_jail_creation_oserror(tmp_path: Path) -> None:
    source = _write_suite(tmp_path / "suite")
    # workspace_root is a *file*, not a directory: copytree's makedirs cannot
    # create a jail path nested "inside" it, so it raises NotADirectoryError.
    workspace_root = tmp_path / "workspaces"
    workspace_root.write_text("not a directory")
    runner = MagicMock()
    runner.run = AsyncMock(return_value=_ok_result())
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(workspace_root))

    with pytest.raises(RunnerError, match="workspace jail creation failed"):
        await executor.execute_pytest_shard(
            proof=_proof(),
            cmd=["python", "-m", "pytest", "-q"],
            cwd=str(source),
        )
    runner.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_worker_executor_raises_when_source_workspace_missing(
    tmp_path: Path,
) -> None:
    runner = MagicMock()
    runner.run = AsyncMock(return_value=_ok_result())
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(tmp_path / "workspaces"))

    with pytest.raises(RunnerError):
        await executor.execute_pytest_shard(
            proof=_proof(),
            cmd=["python", "-m", "pytest", "-q"],
            cwd=str(tmp_path / "does-not-exist"),
        )
    runner.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_worker_executor_isolates_consecutive_attempts(tmp_path: Path) -> None:
    """T-M4-ISOLATE-001: what one Attempt's container writes into its jail must
    not be visible inside a later Attempt's jail, even when both are jailed
    from the same shared source suite."""
    source = _write_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    marker_seen_by_second_attempt = []

    async def _first_attempt_writes_marker(**kwargs):
        Path(kwargs["cwd"], "secret_marker.txt").write_text("attempt-1-secret")
        return _ok_result()

    async def _second_attempt_checks_for_marker(**kwargs):
        marker_seen_by_second_attempt.append(Path(kwargs["cwd"], "secret_marker.txt").exists())
        return _ok_result()

    runner = MagicMock()
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(workspace_root))

    runner.run = AsyncMock(side_effect=_first_attempt_writes_marker)
    await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-001", fence=1),
        cmd=["python", "-m", "pytest", "-q"],
        cwd=str(source),
    )

    runner.run = AsyncMock(side_effect=_second_attempt_checks_for_marker)
    await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-002", fence=1),
        cmd=["python", "-m", "pytest", "-q"],
        cwd=str(source),
    )

    assert marker_seen_by_second_attempt == [False]


@pytest.mark.asyncio
async def test_docker_worker_executor_retried_fence_gets_fresh_workspace(
    tmp_path: Path,
) -> None:
    """A retry that reuses attempt_id but bumps fence must not see the prior
    fence's leftovers, even if the prior jail somehow survived a hard crash
    (cleanup did not run)."""
    source = _write_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    stale_jail = workspace_root / "run-001/assignment-001/attempt-001/1"
    stale_jail.mkdir(parents=True)
    (stale_jail / "leftover.txt").write_text("stale from a crashed attempt")

    runner = MagicMock()
    runner.run = AsyncMock(return_value=_ok_result())
    executor = DockerWorkerExecutor(runner=runner, workspace_root=str(workspace_root))

    await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-001", fence=2),
        cmd=["python", "-m", "pytest", "-q"],
        cwd=str(source),
    )

    jailed_cwd = runner.run.await_args.kwargs["cwd"]
    assert jailed_cwd == str(workspace_root / "run-001/assignment-001/attempt-001/2")
    assert not Path(jailed_cwd, "leftover.txt").exists()


def test_list_residual_sandbox_observations_maps_labeled_containers() -> None:
    """T-M4-RESTART-001: residual listing is pure observation over M4 labels."""
    from qarunner.adapters.docker_worker_executor import list_residual_sandbox_observations
    from qarunner.domain import ResidualSandboxObservation

    class _C:
        def __init__(self, cid: str, labels: dict, status: str = "running") -> None:
            self.id = cid
            self.labels = labels
            self.status = status

    full_labels = {
        "qarunner.run_id": "run-001",
        "qarunner.assignment_id": "assignment-001",
        "qarunner.attempt_id": "attempt-001",
        "qarunner.fence": "2",
        "qarunner.worker_id": "worker-001",
        "qarunner.worker_generation": "1",
        "qarunner.start_commit_key": "commit-key-1",
    }
    incomplete = {"qarunner.attempt_id": "attempt-x"}  # missing the rest
    bad_fence = {**full_labels, "qarunner.fence": "not-int"}

    client = MagicMock()
    client.containers.list.return_value = [
        _C("ctr-good", full_labels, "running"),
        _C("ctr-incomplete", incomplete, "exited"),
        _C("ctr-bad-fence", bad_fence, "running"),
        _C("ctr-stopped", {**full_labels, "qarunner.attempt_id": "attempt-002"}, "exited"),
    ]

    observations = list_residual_sandbox_observations(client, worker_id="worker-001")
    assert client.containers.list.call_args.kwargs["all"] is True
    assert "label" in client.containers.list.call_args.kwargs["filters"]

    assert len(observations) == 2
    assert all(isinstance(o, ResidualSandboxObservation) for o in observations)
    by_attempt = {o.attempt_id: o for o in observations}
    assert by_attempt["attempt-001"].container_id == "ctr-good"
    assert by_attempt["attempt-001"].fence == 2
    assert by_attempt["attempt-001"].running is True
    assert by_attempt["attempt-002"].running is False
    assert "attempt-x" not in by_attempt


def test_list_residual_sandbox_observations_without_worker_filter() -> None:
    from qarunner.adapters.docker_worker_executor import list_residual_sandbox_observations

    client = MagicMock()
    client.containers.list.return_value = []
    assert list_residual_sandbox_observations(client) == ()
    assert client.containers.list.call_args.kwargs["filters"] == {"label": ["qarunner.attempt_id"]}
