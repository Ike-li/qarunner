"""Tests for SubprocessRunner adapter."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


async def test_platform_secrets_stripped(runner: SubprocessRunner, monkeypatch) -> None:
    # SEC-3: QARUNNER_* secrets must not leak into the untrusted child process.
    monkeypatch.setenv("QARUNNER_SECRET_KEY", "leaked-secret")
    code = "import os; print(os.environ.get('QARUNNER_SECRET_KEY', 'ABSENT'))"
    result = await runner.run([sys.executable, "-c", code], cwd=".")
    assert result.exit_code == 0
    assert "leaked-secret" not in result.stdout
    assert "ABSENT" in result.stdout


async def test_non_allowlisted_host_env_not_forwarded(
    runner: SubprocessRunner, monkeypatch
) -> None:
    # SEC-3: a host secret under an arbitrary (non-QARUNNER_) name — e.g. AWS_*,
    # *_TOKEN, DATABASE_URL — must NOT leak into the untrusted child. Only the
    # explicit allowlist passes through; a blocklist (strip QARUNNER_* only)
    # would forward this and is what this test guards against.
    monkeypatch.setenv("MY_CLOUD_SECRET", "host-credential-must-not-leak")
    code = (
        "import os;"
        "print('SECRET=' + os.environ.get('MY_CLOUD_SECRET', 'ABSENT'));"
        "print('PATH_PRESENT=' + ('yes' if os.environ.get('PATH') else 'no'))"
    )
    result = await runner.run([sys.executable, "-c", code], cwd=".")
    assert result.exit_code == 0
    assert "host-credential-must-not-leak" not in result.stdout
    assert "SECRET=ABSENT" in result.stdout
    # An allowlisted functional var (PATH) still passes through so tooling works.
    assert "PATH_PRESENT=yes" in result.stdout


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


async def test_stdout_stderr_files(runner: SubprocessRunner, tmp_path: Path) -> None:
    stdout_file = tmp_path / "logs" / "stdout.log"
    stderr_file = tmp_path / "logs" / "stderr.log"

    result = await runner.run(
        [sys.executable, "-c", "import sys; print('hello file'); sys.stderr.write('error file')"],
        cwd=".",
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
    )

    assert result.exit_code == 0
    assert result.stdout == "hello file\n"
    assert result.stderr == "error file"
    assert stdout_file.exists()
    assert stderr_file.exists()
    assert stdout_file.read_text() == "hello file\n"
    assert stderr_file.read_text() == "error file"


async def test_capture_is_tail_bounded(
    runner: SubprocessRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SEC-8: only the tail of a large log is captured into ProcessResult; the
    # on-disk file stays complete.
    from qarunner.adapters import subprocess_runner as sr

    monkeypatch.setattr(sr, "_MAX_CAPTURE_BYTES", 20)
    stdout_file = tmp_path / "stdout.log"
    result = await runner.run(
        [sys.executable, "-c", "print('HEAD' + 'x' * 1000 + 'TAIL')"],
        cwd=".",
        stdout_file=str(stdout_file),
    )
    assert result.exit_code == 0
    assert len(result.stdout) <= 20
    assert "TAIL" in result.stdout
    assert "HEAD" not in result.stdout
    assert stdout_file.read_text().startswith("HEAD")  # disk file is complete


async def test_timeout_with_files(runner: SubprocessRunner, tmp_path: Path) -> None:
    stdout_file = tmp_path / "stdout.log"
    stderr_file = tmp_path / "stderr.log"

    result = await runner.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        cwd=".",
        timeout=1,
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
    )

    assert result.timed_out is True


@pytest.mark.asyncio
async def test_subprocess_escalation_mocked() -> None:
    runner = SubprocessRunner()

    mock_proc = MagicMock()
    mock_proc.returncode = -9
    mock_proc.send_signal.side_effect = AttributeError("No send_signal")
    mock_proc.wait = AsyncMock()

    mock_proc.communicate = AsyncMock(side_effect=[
        TimeoutError(),  # first wait_for (outer)
        TimeoutError(),  # second wait_for (after SIGINT)
        TimeoutError(),  # third wait_for (after SIGTERM)
        (b"stdout final", b"stderr final")  # final communicate after kill
    ])

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await runner.run(
            ["some_cmd"],
            cwd=".",
            timeout=1,
        )
        assert result.timed_out is True
        assert result.stdout == "stdout final"
        assert result.stderr == "stderr final"
        mock_proc.send_signal.assert_called_once()
        mock_proc.terminate.assert_called()
        mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_subprocess_file_escalation_mocked(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    stdout_file = tmp_path / "stdout.log"

    mock_proc = MagicMock()
    mock_proc.returncode = -15
    mock_proc.wait = AsyncMock(side_effect=[
        TimeoutError(),  # first wait_for (outer)
        TimeoutError(),  # second wait_for
        TimeoutError(),  # third wait_for
        0  # wait after kill
    ])

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await runner.run(
            ["some_cmd"],
            cwd=".",
            timeout=1,
            stdout_file=str(stdout_file),
        )
        assert result.timed_out is True
        mock_proc.send_signal.assert_called_once()
        mock_proc.terminate.assert_called()
        mock_proc.kill.assert_called_once()


