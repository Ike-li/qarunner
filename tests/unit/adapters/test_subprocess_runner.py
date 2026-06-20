"""Tests for SubprocessRunner adapter."""

from __future__ import annotations

import sys

import pytest

from qarunner.adapters.subprocess_runner import SubprocessRunner
from qarunner.errors import RunnerError


@pytest.fixture
def runner() -> SubprocessRunner:
    return SubprocessRunner()


async def test_success(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "print('hello')"],
        cwd=".",
    )
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.duration_ms >= 0
    assert result.timed_out is False


async def test_nonzero_exit(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "import sys; sys.exit(1)"],
        cwd=".",
    )
    assert result.exit_code == 1
    assert result.timed_out is False


async def test_timeout(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=".",
        timeout=1,
    )
    assert result.timed_out is True


async def test_executable_not_found(runner: SubprocessRunner) -> None:
    with pytest.raises(RunnerError, match=""):
        await runner.run(
            ["/nonexistent/binary"],
            cwd=".",
        )


async def test_env_forwarded(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "import os; print(os.environ.get('QA_TEST_VAR', ''))"],
        cwd=".",
        env={"QA_TEST_VAR": "sentinel_value"},
    )
    assert result.exit_code == 0
    assert "sentinel_value" in result.stdout


async def test_stderr_captured(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('err_msg')"],
        cwd=".",
    )
    assert "err_msg" in result.stderr


async def test_duration_ms_is_positive(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "pass"],
        cwd=".",
    )
    assert result.duration_ms >= 0


async def test_graceful_timeout_sigterm(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=".",
        timeout=1,
    )
    assert result.timed_out is True
    assert result.exit_code != 0


async def test_forceful_timeout_sigkill(runner: SubprocessRunner) -> None:
    result = await runner.run(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)",
        ],
        cwd=".",
        timeout=1,
    )
    assert result.timed_out is True
    assert result.exit_code != 0

