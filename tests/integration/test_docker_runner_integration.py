"""Real-Docker integration tests for :class:`DockerRunner` (TEST-1 / SEC-3 / DEP-1).

These exercise the *real* container lifecycle — no mocks — to verify what the
unit suite cannot: that source/results actually round-trip through
put_archive/get_archive onto the host, that real exit codes propagate, and
that the SEC-3 isolation flags take effect on the container DockerRunner
truly launches (asserted from its live ``docker inspect`` attributes rather
than from the kwargs we hand it). ``cmd`` never includes an absolute
``cwd``-rooted path: DockerRunner injects source into a fixed in-container
workspace via put_archive rather than bind-mounting *cwd* at its host path,
so pytest/Playwright must collect via ``working_dir`` (relative), not an
absolute positional path argument.

Opt-in: they need a running Docker daemon and the ``qarunner-executor:latest``
image (built on demand by ``DockerRunner._ensure_image``). They carry the
``docker`` marker, are excluded from the default coverage gate, and run with::

    uv run pytest -m docker --no-cov
"""

from __future__ import annotations

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
    before DockerRunner removes it in its ``finally`` block. DockerRunner now
    calls ``create()`` (not ``run()``) — the sandbox's own command is a
    `sleep` placeholder, and the real command runs via exec after
    put_archive — but HostConfig/Config (what SEC-3 asserts against) are
    fixed at create time regardless."""

    def __init__(self, real, sink: dict) -> None:
        self._real = real
        self._sink = sink

    def create(self, *args, **kwargs):
        container = self._real.create(*args, **kwargs)
        self._sink["create_kwargs"] = kwargs
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
    """A passing suite → exit 0, junit.xml round-trips back to the host via
    get_archive, and the container's exec output is streamed to the on-disk
    log file."""
    tests_dir = tmp_path / "suite"
    _write_test(tests_dir, "def test_pass():\n    assert 1 + 1 == 2\n")
    results_dir = tmp_path / "results"
    stdout_file = tmp_path / "logs" / "stdout.log"
    stderr_file = tmp_path / "logs" / "stderr.log"

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["python", "-m", "pytest", f"--junitxml={results_dir}/junit.xml"],
        cwd=str(tests_dir),
        timeout=180,
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
    )

    # Real exit code from the container.
    assert result.exit_code == 0
    assert result.timed_out is False
    # put_archive/get_archive round-trip worked: the report the container
    # wrote inside its own workspace landed back on the host.
    assert (results_dir / "junit.xml").is_file()
    # Logs landed on disk and match the ProcessResult.
    assert stdout_file.is_file()
    assert "1 passed" in stdout_file.read_text()
    assert "1 passed" in result.stdout


@pytest.mark.asyncio
async def test_real_container_runs_playwright_and_lands_junit(docker_client, tmp_path):
    """A minimal Playwright suite runs in the Playwright executor image and
    writes junit.xml through PLAYWRIGHT_JUNIT_OUTPUT_NAME."""
    tests_dir = tmp_path / "playwright-suite"
    tests_dir.mkdir(parents=True)
    (tests_dir / "smoke.spec.js").write_text(
        "const { test, expect } = require('@playwright/test');\n"
        "test('math works', async () => {\n"
        "  expect(1 + 1).toBe(2);\n"
        "});\n"
    )
    results_dir = tmp_path / "pw-results"

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd=str(tests_dir),
        env={"PLAYWRIGHT_JUNIT_OUTPUT_NAME": f"{results_dir}/junit.xml"},
        timeout=300,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert (results_dir / "junit.xml").is_file()
    assert "math works" in (results_dir / "junit.xml").read_text()


@pytest.mark.asyncio
async def test_real_container_failing_test_returns_nonzero(docker_client, tmp_path):
    """A failing suite propagates pytest's exit code 1 from the real container."""
    tests_dir = tmp_path / "suite"
    _write_test(tests_dir, "def test_fail():\n    assert False\n")

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["python", "-m", "pytest"],
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
        ["python", "-m", "pytest"],
        cwd=str(tests_dir),
        timeout=180,
    )

    attrs = sink["attrs"]
    host = attrs["HostConfig"]
    config = attrs["Config"]

    # Non-root: always the executor image's baked user, whatever uid the calling
    # process has — the dev backend itself runs as root.
    assert config["User"] == "1000:1000"
    # No network.
    assert host["NetworkMode"] == "none"
    # All Linux capabilities dropped.
    assert host["CapDrop"] == ["ALL"]
    # No privilege escalation.
    assert any("no-new-privileges" in opt for opt in (host.get("SecurityOpt") or []))
    # Resource limits (pids_limit=512, mem_limit="2g", nano_cpus=2 cores).
    assert host["PidsLimit"] == 512
    assert host["Memory"] == 2 * 1024**3
    assert host["NanoCpus"] == 2_000_000_000
    # Read-only root filesystem, with a writable /tmp tmpfs for pytest.
    assert host["ReadonlyRootfs"] is True
    assert "/tmp" in (host.get("Tmpfs") or {})


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
        ["python", "-m", "pytest", "-q"],
        cwd=str(tests_dir),
        timeout=180,
    )
    # The network call raises (OSError: Network is unreachable) → pytest fails.
    assert result.exit_code != 0
    assert "Network is unreachable" in result.stdout or "unreachable" in result.stdout.lower()


@pytest.mark.asyncio
async def test_real_container_stdout_bounded_in_memory_but_full_on_disk(docker_client, tmp_path):
    """T-M4-RESOURCE-001: a real container emitting far more than
    _MAX_LOG_BYTES of stdout must not let the returned ProcessResult grow
    unbounded (protecting the platform's own process from an OOM), while the
    full output still lands on stdout_file for anyone who needs all of it."""
    from qarunner.adapters.docker_runner import _MAX_LOG_BYTES

    tests_dir = tmp_path / "suite"
    huge_size = _MAX_LOG_BYTES + 2_000_000
    _write_test(
        tests_dir,
        f"def test_emits_huge_output():\n    print('x' * {huge_size})\n",
    )
    stdout_file = tmp_path / "logs" / "stdout.log"

    runner = DockerRunner(client=docker_client)
    result = await runner.run(
        ["python", "-m", "pytest", "-s"],
        cwd=str(tests_dir),
        timeout=180,
        stdout_file=str(stdout_file),
    )

    assert result.exit_code == 0
    assert len(result.stdout.encode()) <= _MAX_LOG_BYTES
    # The full output landed on disk even though memory was bounded.
    assert stdout_file.stat().st_size > _MAX_LOG_BYTES
