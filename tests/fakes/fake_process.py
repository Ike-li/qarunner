"""Fake process runner for testing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from qarunner.models import ProcessResult


@dataclass
class FakeProcessRunner:
    """In-memory ProcessRunner backed by a user-supplied handler callable."""

    handler: Callable[[list[str], str, dict[str, str] | None, int], ProcessResult]
    calls: list[tuple[list[str], str, dict[str, str] | None, int]] = field(
        default_factory=list,
    )

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
        stdout_file: str | None = None,
        stderr_file: str | None = None,
    ) -> ProcessResult:
        self.calls.append((cmd, cwd, env, timeout))
        return self.handler(cmd, cwd, env, timeout)
