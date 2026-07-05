"""playwright runner — builds the npx playwright test command line."""

from __future__ import annotations

from qarunner.core.runners.base import BuildContext


class PlaywrightRunner:
    """Builds `npx playwright test` CLI commands with junit reporter.

    JUnit output path is controlled by the ``PLAYWRIGHT_JUNIT_OUTPUT_NAME``
    environment variable (injected by the orchestrator at execute time), not
    by a CLI flag (``--output`` is the *artifact* directory, not the reporter
    output path).
    """

    @property
    def name(self) -> str:
        return "playwright"

    def build_command(self, ctx: BuildContext) -> list[str]:
        cmd = [
            "npx",
            "playwright",
            "test",
            "--reporter=junit",
            f"--output={ctx.results_dir}/playwright-results",
        ]
        # selected_files (spec names) appended as positional args
        cmd.extend(ctx.args)
        return cmd
