"""Process execution port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qarunner.models import ProcessResult


@runtime_checkable
class ProcessRunner(Protocol):
    """Port for running external subprocesses."""

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
        stdout_file: str | None = None,
        stderr_file: str | None = None,
    ) -> ProcessResult: ...

