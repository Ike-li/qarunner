"""In-memory WorkerExecutor Fake for M4 admission tests.

Does not call Docker. Records admitted executions for assertions.
"""

from __future__ import annotations

from dataclasses import dataclass

from qarunner.application.ports.worker_execution import (
    WorkerExecutionResult,
    WorkerExecutor,
)
from qarunner.domain.worker_execution import (
    CommitStartProof,
    require_execution_admission,
    sandbox_labels_for_proof,
)


@dataclass
class RecordedExecution:
    proof: CommitStartProof
    cmd: list[str]
    cwd: str
    timeout: int
    labels: dict[str, str]


class InMemoryWorkerExecutor(WorkerExecutor):
    def __init__(self) -> None:
        self.executions: list[RecordedExecution] = []

    async def execute_pytest_shard(
        self,
        *,
        proof: CommitStartProof,
        cmd: list[str],
        cwd: str,
        timeout: int = 1800,
        env: dict[str, str] | None = None,
    ) -> WorkerExecutionResult:
        admitted = require_execution_admission(proof=proof)
        labels = sandbox_labels_for_proof(admitted)
        self.executions.append(
            RecordedExecution(
                proof=admitted,
                cmd=list(cmd),
                cwd=cwd,
                timeout=timeout,
                labels=labels,
            )
        )
        return WorkerExecutionResult(exit_code=0, timed_out=False, labels=labels)
