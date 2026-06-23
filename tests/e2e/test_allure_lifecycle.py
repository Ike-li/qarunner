"""E2E: full run lifecycle through RunOrchestrator.execute, ending in a real
Allure HTML report (TEST-1).

The unit suite fakes the reporter, so the headline feature — Allure report
generation — was never exercised end to end. This drives the *real* pipeline:
real subprocess pytest (with the allure-pytest plugin) → real JunitCollector →
real AllureCliReporter shelling out to the ``allure`` CLI, then asserts the run
reaches COMPLETED with ``html_generated`` and an ``index.html`` on disk.

Marked ``@pytest.mark.e2e`` (needs real pytest + the allure CLI); excluded from
the coverage gate. Run with ``uv run pytest -m e2e --no-cov``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from qarunner.adapters.allure_cli_reporter import AllureCliReporter
from qarunner.adapters.junit_collector import JunitCollector
from qarunner.adapters.subprocess_runner import SubprocessRunner
from qarunner.adapters.system_clock import SystemClock
from qarunner.adapters.uuid_ids import UuidIds
from qarunner.core.orchestrator import RunOrchestrator
from qarunner.core.runners.pytest_runner import PytestRunner
from qarunner.core.runners.registry import RunnerRegistry
from qarunner.models import RunRequest, RunStatus
from tests.fakes.fake_scheduler import FakeScheduler
from tests.fakes.fake_store import InMemoryRunStore

pytestmark = pytest.mark.e2e


def _real_orchestrator(
    tests_root: Path, artifacts_root: Path
) -> tuple[RunOrchestrator, InMemoryRunStore]:
    registry = RunnerRegistry()
    registry.register(PytestRunner())
    store = InMemoryRunStore()
    orch = RunOrchestrator(
        registry=registry,
        store=store,
        scheduler=FakeScheduler(),
        process=SubprocessRunner(),
        collector=JunitCollector(),
        # Real reporter shelling out to the allure CLI via its own runner.
        reporter=AllureCliReporter(process=SubprocessRunner(), allure_bin="allure"),
        clock=SystemClock(),
        ids=UuidIds(),
        tests_root=str(tests_root),
        artifacts_root=str(artifacts_root),
        executable=sys.executable,
        default_timeout=120,
    )
    return orch, store


@pytest.mark.asyncio
async def test_full_run_lifecycle_generates_allure_html(tmp_path):
    """create() → execute() produces a real single-file Allure report on disk."""
    tests_root = tmp_path / "tests_root"
    tests_root.mkdir()
    src = Path(__file__).resolve().parents[2] / "examples" / "sample_tests"
    shutil.copytree(src, tests_root / "sample_tests")
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()

    orch, store = _real_orchestrator(tests_root, artifacts_root)

    run = await orch.create(RunRequest(tests_path="sample_tests"))
    await orch.execute(run.id)

    final = await store.get(run.id)
    # junit was collected → terminal state is COMPLETED (sample_tests has a
    # failing case, but a collected result still completes the run).
    assert final.status == RunStatus.COMPLETED
    assert final.report is not None
    # The headline assertion: the allure CLI actually produced HTML.
    assert final.report.html_generated is True
    report_file = Path(final.report.allure_report_file)
    assert report_file.is_file(), f"expected report at {report_file}"
    assert report_file.name == "index.html"
    assert report_file.stat().st_size > 0
    # The raw allure results the report was built from also exist on disk.
    results_dir = artifacts_root / run.id / "results"
    assert (results_dir / "allure-results").is_dir()
    assert any((results_dir / "allure-results").iterdir())
