"""Tests for AllureCliReporter adapter."""

from __future__ import annotations

from pathlib import Path

from qarunner.adapters.allure_cli_reporter import AllureCliReporter
from qarunner.models import ProcessResult


class _FakeProcess:
    """Minimal fake for testing AllureCliReporter."""

    def __init__(self, result: ProcessResult) -> None:
        self._result = result
        self.calls: list[tuple[list[str], str, dict[str, str] | None, int]] = []

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
    ) -> ProcessResult:
        self.calls.append((cmd, cwd, env, timeout))
        return self._result


async def test_disabled_returns_ref(tmp_path: Path) -> None:
    fake = _FakeProcess(ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=10))
    reporter = AllureCliReporter(fake)
    result = await reporter.generate(str(tmp_path), enabled=False)
    assert result.allure_results_dir == str(tmp_path)
    assert result.allure_report_file is None
    assert result.html_generated is False
    assert len(fake.calls) == 0


async def test_no_allure_results_dir_returns_ref(tmp_path: Path) -> None:
    fake = _FakeProcess(ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=10))
    reporter = AllureCliReporter(fake)
    result = await reporter.generate(str(tmp_path), enabled=True)
    assert result.allure_results_dir == str(tmp_path)
    assert result.allure_report_file is None
    assert result.html_generated is False


async def test_successful_generate(tmp_path: Path) -> None:
    allure_results = tmp_path / "allure-results"
    allure_results.mkdir()
    (allure_results / "test-result.json").write_text("{}")

    fake = _FakeProcess(ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=100))
    reporter = AllureCliReporter(fake, allure_bin="/usr/bin/allure")
    result = await reporter.generate(str(tmp_path), enabled=True)

    assert result.html_generated is True
    assert result.allure_report_file == f"{tmp_path}/allure-report/index.html"
    assert len(fake.calls) == 1
    cmd = fake.calls[0][0]
    assert cmd[0] == "/usr/bin/allure"
    assert "generate" in cmd


async def test_nonzero_exit_returns_ref(tmp_path: Path) -> None:
    allure_results = tmp_path / "allure-results"
    allure_results.mkdir()
    (allure_results / "test-result.json").write_text("{}")

    fake = _FakeProcess(
        ProcessResult(exit_code=1, stdout="error", stderr="err", duration_ms=50)
    )
    reporter = AllureCliReporter(fake)
    result = await reporter.generate(str(tmp_path), enabled=True)

    assert result.allure_report_file is None
    assert result.html_generated is False


async def test_process_exception_returns_ref(tmp_path: Path) -> None:
    allure_results = tmp_path / "allure-results"
    allure_results.mkdir()
    (allure_results / "test-result.json").write_text("{}")

    class _ExplodingProcess:
        async def run(
            self,
            cmd: list[str],
            cwd: str,
            env: dict[str, str] | None = None,
            timeout: int = 1800,
        ) -> ProcessResult:
            raise RuntimeError("boom")

    reporter = AllureCliReporter(_ExplodingProcess())  # type: ignore[arg-type]
    result = await reporter.generate(str(tmp_path), enabled=True)
    assert result.allure_report_file is None
    assert result.html_generated is False


async def test_empty_allure_results_dir_returns_ref(tmp_path: Path) -> None:
    allure_results = tmp_path / "allure-results"
    allure_results.mkdir()
    # Empty dir — should_generate returns False

    fake = _FakeProcess(ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=10))
    reporter = AllureCliReporter(fake)
    result = await reporter.generate(str(tmp_path), enabled=True)
    assert result.allure_report_file is None
