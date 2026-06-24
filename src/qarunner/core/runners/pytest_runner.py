"""pytest runner — builds the pytest command line."""

from __future__ import annotations

from qarunner.core.runners.base import BuildContext


class PytestRunner:
    """Builds pytest CLI commands with junitxml + alluredir flags."""

    @property
    def name(self) -> str:
        return "pytest"

    def build_command(self, ctx: BuildContext) -> list[str]:
        cmd = [
            ctx.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            f"--junitxml={ctx.results_dir}/junit.xml",
            f"--alluredir={ctx.results_dir}/allure-results",
        ]
        cmd.extend(ctx.args)
        return cmd
