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

import asyncio
import time
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


@pytest.mark.asyncio
async def test_memory_limit_is_enforced(docker_client, tmp_path):
    """T-M4-RESOURCE-001: allocating past the sandbox's mem_limit gets the
    Attempt OOM-killed by the cgroup rather than allowed to grow unbounded.
    This also covers the tmp-disk bound: tmpfs pages are charged to the same
    memory cgroup (confirmed empirically — writing to a size-less tmpfs past
    mem_limit triggers the identical OOM kill, not unbounded host growth)."""
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_oom.py").write_text(
        "def test_allocates_beyond_mem_limit():\n"
        "    data = bytearray(3 * 1024**3)\n"  # 3 GiB > the sandbox's 2g mem_limit
        "    data[0] = 1\n"
        "    assert len(data) > 0\n"
    )
    executor = DockerWorkerExecutor(
        runner=DockerRunner(client=docker_client),
        workspace_root=str(tmp_path / "workspaces"),
    )

    result = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-oom", fence=1),
        cmd=["python", "-m", "pytest", "-k", "test_allocates_beyond_mem_limit"],
        cwd=str(source),
        timeout=60,
    )

    # OOM-killed by the cgroup — not a graceful pytest assertion failure, and
    # not exit 0 as it would be if the 2g cap weren't actually enforced.
    assert result.exit_code != 0
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_pid_limit_is_enforced(docker_client, tmp_path):
    """T-M4-RESOURCE-001: forking past the sandbox's 512 pids_limit fails
    inside the sandbox rather than being allowed to exhaust the host's
    process table. The inner pytest assertion only passes if fork() actually
    hit EAGAIN, so a regression that dropped pids_limit would fail this test
    (not silently report success)."""
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_pid_limit.py").write_text(
        "import errno\n"
        "import os\n"
        "\n"
        "def test_fork_beyond_pids_limit():\n"
        "    hit_limit = False\n"
        "    pids = []\n"
        "    try:\n"
        "        for _ in range(600):\n"
        "            pid = os.fork()\n"
        "            if pid == 0:\n"
        "                os._exit(0)\n"
        "            pids.append(pid)\n"
        "    except (BlockingIOError, OSError) as exc:\n"
        "        if exc.errno == errno.EAGAIN:\n"
        "            hit_limit = True\n"
        "    finally:\n"
        "        for pid in pids:\n"
        "            try:\n"
        "                os.waitpid(pid, 0)\n"
        "            except ChildProcessError:\n"
        "                pass\n"
        "    assert hit_limit, 'expected fork() to fail once pids_limit was exceeded'\n"
    )
    executor = DockerWorkerExecutor(
        runner=DockerRunner(client=docker_client),
        workspace_root=str(tmp_path / "workspaces"),
    )

    result = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-pid", fence=1),
        cmd=["python", "-m", "pytest", "-k", "test_fork_beyond_pids_limit"],
        cwd=str(source),
        timeout=60,
    )

    assert result.exit_code == 0
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_hard_timeout_is_enforced(docker_client, tmp_path):
    """T-M4-RESOURCE-001: a command that runs past the given timeout is
    actually killed near the deadline, not left to run to completion."""
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_slow.py").write_text(
        "import time\n\ndef test_sleeps_past_timeout():\n    time.sleep(120)\n"
    )
    executor = DockerWorkerExecutor(
        runner=DockerRunner(client=docker_client),
        workspace_root=str(tmp_path / "workspaces"),
    )

    start = time.monotonic()
    result = await executor.execute_pytest_shard(
        proof=_proof(attempt_id="attempt-timeout", fence=1),
        cmd=["python", "-m", "pytest", "-k", "test_sleeps_past_timeout"],
        cwd=str(source),
        timeout=5,
    )
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert result.exit_code == 137
    # Actually enforced near the 5s deadline, nowhere close to the full 120s
    # sleep the test body asked for.
    assert elapsed < 60


@pytest.mark.asyncio
async def test_cancelling_the_task_stops_the_real_container(docker_client, tmp_path):
    """T-M4-CANCEL-001: cancelling the asyncio Task awaiting
    execute_pytest_shard — the same mechanism the legacy orchestrator's own
    cancel() already relies on (propagating CancelledError into the running
    execution so its runner's finally block kills the sandbox) — actually
    stops the real container within a bounded time, not left running for the
    rest of its original timeout budget."""
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_slow.py").write_text(
        "import time\n\ndef test_runs_long():\n    time.sleep(120)\n"
    )
    workspace_root = tmp_path / "workspaces"
    executor = DockerWorkerExecutor(
        runner=DockerRunner(client=docker_client),
        workspace_root=str(workspace_root),
    )
    label_filter = {"label": "qarunner.attempt_id=attempt-cancel-001"}

    task = asyncio.create_task(
        executor.execute_pytest_shard(
            proof=_proof(attempt_id="attempt-cancel-001", fence=1),
            cmd=["python", "-m", "pytest", "-k", "test_runs_long"],
            cwd=str(source),
            timeout=180,
        )
    )

    # Let the sandbox actually reach "running" before cancelling — proving
    # cancellation stops a real in-flight container, not just an unstarted one.
    deadline = time.monotonic() + 30
    running = []
    while time.monotonic() < deadline:
        running = docker_client.containers.list(filters=label_filter)
        if running:
            break
        await asyncio.sleep(0.5)
    assert running, "expected the sandbox container to appear before cancelling"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Bounded cleanup window — comfortably inside even the design doc's own
    # "cancel soft grace 10s / hard cap 120s" bootstrap targets (§6.6),
    # since a force-kill+remove has no graceful-stop wait built in.
    deadline = time.monotonic() + 15
    remaining = running
    while time.monotonic() < deadline:
        remaining = docker_client.containers.list(all=True, filters=label_filter)
        if not remaining:
            break
        await asyncio.sleep(0.5)
    assert not remaining, "sandbox container was not cleaned up after cancellation"

    # The workspace jail is also cleaned up, not leaked.
    assert not (
        workspace_root / "run-isolate-001/assignment-isolate-001/attempt-cancel-001/1"
    ).exists()
