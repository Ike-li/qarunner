"""E2E smoke tests — run real pytest via subprocess, verify junit parsing.

These tests are marked with ``@pytest.mark.e2e`` and are NOT counted in the
unit-test coverage gate.  Run them separately with ``uv run pytest -m e2e``.
"""

from __future__ import annotations

import sys

import pytest

from qarunner.adapters.junit_collector import JunitCollector
from qarunner.adapters.subprocess_runner import SubprocessRunner

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_run_sample_tests_and_collect_results(tmp_path):
    """Run the sample_tests through real subprocess + junit collection."""
    runner = SubprocessRunner()
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    sample_dir = str(tmp_path / "examples" / "sample_tests")

    # Copy sample tests to tmp to avoid polluting project
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "examples" / "sample_tests"
    shutil.copytree(src, sample_dir)

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        f"--junitxml={results_dir}/junit.xml",
        sample_dir,
    ]

    proc = await runner.run(cmd, cwd=str(tmp_path), timeout=30)
    # sample_tests has 1 failing test, so exit code should be 1
    assert proc.exit_code == 1
    assert proc.timed_out is False

    # Collect results
    collector = JunitCollector()
    result = collector.collect(str(results_dir))
    assert result is not None
    assert result.summary.total == 3
    assert result.summary.passed == 2
    assert result.summary.failed == 1
    assert result.summary.pass_rate == pytest.approx(2 / 3)
