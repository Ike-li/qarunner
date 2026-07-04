"""Real Allure reporter that shells out to the allure CLI."""

from __future__ import annotations

import logging

from qarunner.core.allure import build_generate_command, should_generate
from qarunner.models import ReportRef
from qarunner.ports.process import ProcessRunner

logger = logging.getLogger(__name__)


class AllureCliReporter:
    """Generate Allure reports by invoking the allure CLI via ProcessRunner."""

    def __init__(self, process: ProcessRunner, allure_bin: str = "allure") -> None:
        self._process = process
        self._allure_bin = allure_bin

    async def generate(self, run_dir: str, enabled: bool = True) -> ReportRef:
        results_dir = run_dir

        if not enabled:
            return ReportRef(allure_results_dir=results_dir)

        if not should_generate(results_dir):
            return ReportRef(allure_results_dir=results_dir)

        cmd = build_generate_command(self._allure_bin, results_dir)

        try:
            proc = await self._process.run(cmd, cwd=".", timeout=300)
        except Exception:  # noqa: BLE001 — allure is best-effort; never fail the run
            logger.warning("Allure report generation failed for %s", results_dir, exc_info=True)
            return ReportRef(allure_results_dir=results_dir)

        if proc.exit_code != 0:
            return ReportRef(allure_results_dir=results_dir)

        report_file = f"{results_dir}/allure-report/index.html"
        return ReportRef(
            allure_results_dir=results_dir,
            allure_report_file=report_file,
            html_generated=True,
        )
