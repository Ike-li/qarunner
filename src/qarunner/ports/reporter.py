"""Allure reporting port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qarunner.models import ReportRef


@runtime_checkable
class AllureReporter(Protocol):
    """Port for generating Allure reports."""

    async def generate(self, run_dir: str, enabled: bool = True) -> ReportRef: ...
