"""Fake result collector for testing."""

from __future__ import annotations

from dataclasses import dataclass, field

from qarunner.models import CollectResult


@dataclass
class FakeResultCollector:
    """Returns a preset CollectResult and records calls."""

    preset: CollectResult | None = None
    calls: list[str] = field(default_factory=list)

    def collect(self, run_dir: str) -> CollectResult | None:
        self.calls.append(run_dir)
        return self.preset
