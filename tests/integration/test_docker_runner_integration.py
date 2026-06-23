"""Real-Docker integration tests for :class:`DockerRunner` (TEST-1 / SEC-3 / DEP-1).

These exercise the *real* container lifecycle — no mocks — to verify what the
unit suite cannot: that bind-mounted artifacts actually land on the host, that
real exit codes propagate, and that the SEC-3 isolation flags take effect on the
container DockerRunner truly launches (asserted from its live ``docker inspect``
attributes rather than from the kwargs we hand it).

Opt-in: they need a running Docker daemon and the ``qarunner-executor:latest``
image (built on demand by ``DockerRunner._ensure_image``). They carry the
``docker`` marker, are excluded from the default coverage gate, and run with::

    uv run pytest -m docker --no-cov
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

docker = pytest.importorskip("docker")

from qarunner.adapters.docker_runner import DockerRunner  # noqa: E402

pytestmark = pytest.mark.docker


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


class _CapturingContainers:
    """Wrap ``client.containers`` so we keep the real container's inspect attrs
    before DockerRunner removes it in its ``finally`` block."""

    def __init__(self, real, sink: dict) -> None:
        self._real = real
        self._sink = sink

    def run(self, *args, **kwargs):
        container = self._real.run(*args, **kwargs)
        # ``attrs`` here is the create-time ``docker inspect`` payload (docker-py
        # inspects the container inside ``containers.run``), so HostConfig/Config
        # are fully populated even though the container is detached.
        self._sink["run_kwargs"] = kwargs
        self._sink["attrs"] = container.attrs
        return container

    def __getattr__(self, name):
        return getattr(self._real, name)


class _CapturingClient:
    """A docker client that records the container DockerRunner launches."""

    def __init__(self, real, sink: dict) -> None:
        self._real = real
        self._sink = sink

    @property
    def containers(self):
        return _CapturingContainers(self._real.containers, self._sink)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _write_test(d: Path, body: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / "test_sample.py"
    f.write_text(body)
    return f


@pytest.mark.asyncio
async def test_real_container_runs_pytest_and_lands_artifacts(docker_client, tmp_path):
    """A passing suite → exit 0, junit.xml written to the host volume, and the
    container stdout streamed to the on-disk log file."""
    tests_dir = tmp_path / "suite"
    _write_test(tests_dir, "def test_pass():\n    assert 1 + 1 == 2\n")
    results_dir = tmp_path / "results"
    stdout_file = tmp_path / "logs" / "stdout.log"
    stderr_file = tmp_path / "logs" / "stderr.log"

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["python", "-m", "pytest", f"--junitxml={results_dir}/junit.xml", str(tests_dir)],
        cwd=str(tests_dir),
        timeout=180,
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
    )

    # Real exit code from the container.
    assert result.exit_code == 0
    assert result.timed_out is False
    # Bind mount worked: the report file the container wrote is on the host.
    assert (results_dir / "junit.xml").is_file()
    # Logs landed on disk and match the ProcessResult.
    assert stdout_file.is_file()
    assert "1 passed" in stdout_file.read_text()
    assert "1 passed" in result.stdout


@pytest.mark.asyncio
async def test_real_container_failing_test_returns_nonzero(docker_client, tmp_path):
    """A failing suite propagates pytest's exit code 1 from the real container."""
    tests_dir = tmp_path / "suite"
    _write_test(tests_dir, "def test_fail():\n    assert False\n")

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["python", "-m", "pytest", str(tests_dir)],
        cwd=str(tests_dir),
        timeout=180,
    )
    assert result.exit_code == 1
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_real_container_sec3_isolation_via_inspect(docker_client, tmp_path):
    """SEC-3: the container DockerRunner launches is non-root, has no network,
    drops all caps, forbids new privileges, and carries pid/mem limits — read
    straight from its live ``docker inspect`` attributes."""
    tests_dir = tmp_path / "suite"
    _write_test(tests_dir, "def test_pass():\n    assert True\n")

    sink: dict = {}
    runner = DockerRunner(client=_CapturingClient(docker_client, sink))
    await runner.run(
        ["python", "-m", "pytest", str(tests_dir)],
        cwd=str(tests_dir),
        timeout=180,
    )

    attrs = sink["attrs"]
    host = attrs["HostConfig"]
    config = attrs["Config"]

    # Non-root: runs as the host caller's uid:gid (so bind-mounted artifacts stay
    # owned by us). NB this is the *host* uid, not a hard-coded 1000:1000.
    assert config["User"] == f"{os.getuid()}:{os.getgid()}"
    assert not config["User"].startswith("0:"), "must not run as root uid 0"
    # No network.
    assert host["NetworkMode"] == "none"
    # All Linux capabilities dropped.
    assert host["CapDrop"] == ["ALL"]
    # No privilege escalation.
    assert any("no-new-privileges" in opt for opt in (host.get("SecurityOpt") or []))
    # Resource limits (pids_limit=512, mem_limit="2g").
    assert host["PidsLimit"] == 512
    assert host["Memory"] == 2 * 1024**3


@pytest.mark.asyncio
async def test_real_container_has_no_network(docker_client, tmp_path):
    """Behavioural proof that ``network_mode=none`` truly isolates the network:
    a socket connect from inside the container fails."""
    tests_dir = tmp_path / "suite"
    # The test body tries to reach the network; with network_mode=none it must
    # fail, so this pytest run exits non-zero.
    _write_test(
        tests_dir,
        "import socket\n"
        "def test_no_network():\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=5)\n",
    )

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["python", "-m", "pytest", "-q", str(tests_dir)],
        cwd=str(tests_dir),
        timeout=180,
    )
    # The network call raises (OSError: Network is unreachable) → pytest fails.
    assert result.exit_code != 0
    assert "Network is unreachable" in result.stdout or "unreachable" in result.stdout.lower()
