"""Result collection port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qarunner.models import CollectResult


@runtime_checkable
class ResultCollector(Protocol):
    """Port for collecting test results from a run directory."""

    def collect(self, run_dir: str) -> CollectResult | None: ...
