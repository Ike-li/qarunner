"""Docker-backed WorkerExecutor (M4).

Requires durable CommitStartProof before creating a container. Labels bind the
sandbox to run/assignment/attempt/fence. Uses existing DockerRunner isolation
defaults (network=none, cap_drop ALL, read-only root).

Same-host residual risk: this adapter still uses the host Docker API; process-
separated Worker agent remains the production-grade boundary.
"""

from __future__ import annotations

from qarunner.adapters.docker_runner import DockerRunner
from qarunner.application.ports.worker_execution import (
    WorkerExecutionResult,
    WorkerExecutor,
)
from qarunner.domain.worker_execution import (
    CommitStartProof,
    require_execution_admission,
    sandbox_labels_for_proof,
)


class DockerWorkerExecutor(WorkerExecutor):
    """Admit pytest shards into labeled Docker sandboxes after commit-start."""

    def __init__(self, runner: DockerRunner | None = None) -> None:
        self._runner = runner if runner is not None else DockerRunner()

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
        process = await self._runner.run(
            cmd=cmd,
            cwd=cwd,
            env=env,
            timeout=timeout,
            labels=labels,
        )
        return WorkerExecutionResult(
            exit_code=process.exit_code,
            timed_out=process.timed_out,
            labels=labels,
        )
