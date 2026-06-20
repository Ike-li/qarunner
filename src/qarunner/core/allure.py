"""Allure CLI helpers — build generate command, decide whether to run."""

from __future__ import annotations

from pathlib import Path


def build_generate_command(
    allure_bin: str,
    results_dir: str,
) -> list[str]:
    """Build the ``allure generate --single-file`` command."""
    return [
        allure_bin,
        "generate",
        f"{results_dir}/allure-results",
        "--single-file",
        "--clean",
        "-o",
        f"{results_dir}/allure-report",
    ]


def should_generate(results_dir: str) -> bool:
    """Return True if allure-results directory has any files."""
    allure_dir = Path(results_dir) / "allure-results"
    if not allure_dir.is_dir():
        return False
    return any(allure_dir.iterdir())
