"""Real-Docker integration tests for :class:`DockerWorkerExecutor` (M4-ISOLATE).

Exercise the *real* container lifecycle through the M4 Worker execution port to
prove T-M4-ISOLATE-001: a consecutive Attempt — including a retry that reuses
attempt_id with a higher fence — can never read a prior Attempt's workspace,
because each Attempt runs inside a freshly jailed copy scoped to its own
run/assignment/attempt/fence identity and that jail is removed after use.

Opt-in: needs a running Docker daemon and the ``qarunner-executor:latest``
image (built on demand). Carries the ``docker`` marker, excluded from the
default coverage gate::

    uv run pytest -m docker --no-cov

These tests run inside the ``backend`` dev container, which reaches the *host*
Docker daemon over a mounted socket (see ``docker-compose.dev.yml``).
DockerRunner injects/extracts source and results via ``put_archive``/
``get_archive`` (not bind mounts), so plain ``tmp_path`` — container-local
only — works fine here without needing any host-path-identical location.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

docker = pytest.importorskip("docker")

from qarunner.adapters.docker_runner import DockerRunner  # noqa: E402
from qarunner.adapters.docker_worker_executor import DockerWorkerExecutor  # noqa: E402
from qarunner.domain import CommitStartProof, WorkerRef, canonical_digest  # noqa: E402

pytestmark = pytest.mark.docker

T0 = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def docker_client():
    """A live docker client, or skip the module if the daemon is unreachable."""
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # daemon down / socket missing
        pytest.skip(f"Docker daemon unavailable: {exc}")
    yield client
    client.close()


def _proof(*, attempt_id: str, fence: int) -> CommitStartProof:
    return CommitStartProof(
        run_id="run-isolate-001",
        assignment_id="assignment-isolate-001",
        attempt_id=attempt_id,
        fence=fence,
        start_commit_key=f"commit-key-{attempt_id}-{fence}",
        worker=WorkerRef(worker_id="worker-isolate-001", generation=1),
        spec_digest=canonical_digest(
            schema_version="qep.execution-spec.v1",
            payload={"label": "isolate"},
        ),
        committed_at=T0,
    )


def _write_shared_suite(root: Path) -> Path:
    """One shared source template reused, unmodified, by every Attempt below."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "test_write_secret.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def test_write_secret():\n"
        "    Path('secret_marker.txt').write_text('attempt-1-secret')\n"
    )
    (root / "test_check_no_secret.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def test_no_secret_leaked():\n"
        "    assert not Path('secret_marker.txt').exists()\n"
    )
    return root


@pytest.mark.asyncio
async def test_consecutive_attempts_cannot_read_prior_workspace(docker_client, tmp_path):
    """T-M4-ISOLATE-001: Attempt 2's real container never sees what Attempt 1's
    real container wrote into its own workspace."""
    source = _write_shared_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    executor = DockerWorkerExecutor(
        runner=DockerRunner(client=docker_client),
        workspace_root=str(workspace_root),
    )

    first = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-001", fence=1),
        cmd=["python", "-m", "pytest", "-k", "write_secret"],
        cwd=str(source),
        timeout=180,
    )
    assert first.exit_code == 0
    assert first.timed_out is False
    assert first.labels["qarunner.attempt_id"] == "attempt-001"

    # Attempt 1's jail is gone; the shared source template was never mutated.
    assert not (workspace_root / "run-isolate-001/assignment-isolate-001/attempt-001/1").exists()
    assert not (source / "secret_marker.txt").exists()

    second = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-002", fence=1),
        cmd=["python", "-m", "pytest", "-k", "no_secret_leaked"],
        cwd=str(source),
        timeout=180,
    )
    # If Attempt 2's jail had somehow inherited Attempt 1's marker, this
    # in-container assertion would fail and exit_code would be nonzero.
    assert second.exit_code == 0
    assert second.timed_out is False


@pytest.mark.asyncio
async def test_retried_fence_cannot_read_prior_fence_workspace(docker_client, tmp_path):
    """A retry that reuses attempt_id with a bumped fence is isolated from the
    prior fence's workspace exactly like a distinct attempt_id would be."""
    source = _write_shared_suite(tmp_path / "suite")
    workspace_root = tmp_path / "workspaces"
    executor = DockerWorkerExecutor(
        runner=DockerRunner(client=docker_client),
        workspace_root=str(workspace_root),
    )

    first = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-retry-001", fence=1),
        cmd=["python", "-m", "pytest", "-k", "write_secret"],
        cwd=str(source),
        timeout=180,
    )
    assert first.exit_code == 0

    retried = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-retry-001", fence=2),
        cmd=["python", "-m", "pytest", "-k", "no_secret_leaked"],
        cwd=str(source),
        timeout=180,
    )
    assert retried.exit_code == 0
