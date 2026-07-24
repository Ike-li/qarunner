"""Worker-side execution port for M4 pytest vertical slice.

Control plane never implements this port with Docker socket access. The Worker
agent (or a test Fake) admits work only after CommitStartProof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from qarunner.domain.worker_execution import CommitStartProof


@dataclass(frozen=True, slots=True)
class WorkerExecutionResult:
    """Observable process outcome of one admitted sandbox execution."""

    exit_code: int
    timed_out: bool
    labels: dict[str, str]


@runtime_checkable
class WorkerExecutor(Protocol):
    """Execute untrusted pytest work only after durable commit-start."""

    async def execute_pytest_shard(
        self,
        *,
        proof: CommitStartProof,
        cmd: list[str],
        cwd: str,
        timeout: int = 1800,
        env: dict[str, str] | None = None,
    ) -> WorkerExecutionResult: ...
