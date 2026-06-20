"""Runner registry — maps runner names to Runner implementations."""

from __future__ import annotations

from qarunner.core.runners.base import Runner
from qarunner.errors import UnknownRunner


class RunnerRegistry:
    """Simple name → Runner mapping."""

    def __init__(self) -> None:
        self._runners: dict[str, Runner] = {}

    def register(self, runner: Runner) -> None:
        self._runners[runner.name] = runner

    def get(self, name: str) -> Runner:
        try:
            return self._runners[name]
        except KeyError:
            raise UnknownRunner(name) from None
