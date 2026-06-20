"""Fake allure reporter for testing."""

from __future__ import annotations

from dataclasses import dataclass, field

from qarunner.models import ReportRef


@dataclass
class FakeAllureReporter:
    """Returns a preset ReportRef and records calls."""

    preset: ReportRef
    calls: list[tuple[str, bool]] = field(default_factory=list)

    async def generate(self, run_dir: str, enabled: bool = True) -> ReportRef:
        self.calls.append((run_dir, enabled))
        return self.preset
