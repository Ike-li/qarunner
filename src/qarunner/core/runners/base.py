"""Runner protocol and build context for test execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BuildContext:
    """Parameters needed to construct a test-runner command line."""

    tests_dir: str
    results_dir: str
    executable: str
    args: list[str]


class Runner(Protocol):
    """A test runner that can build a CLI command."""

    @property
    def name(self) -> str: ...

    def build_command(self, ctx: BuildContext) -> list[str]: ...
