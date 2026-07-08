"""Fake failure analyzer for testing."""

from __future__ import annotations

from dataclasses import dataclass, field

from qarunner.core.failure_analysis import FailureContext
from qarunner.models import FailureDiagnosis


@dataclass
class FakeFailureAnalyzer:
    """Returns a preset FailureDiagnosis and records the contexts it received."""

    preset: FailureDiagnosis
    calls: list[FailureContext] = field(default_factory=list)

    async def analyze(self, context: FailureContext) -> FailureDiagnosis:
        self.calls.append(context)
        return self.preset
