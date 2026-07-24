"""Docker-backed WorkerExecutor (M4).

Requires durable CommitStartProof before creating a container. Labels bind the
sandbox to run/assignment/attempt/fence. Uses existing DockerRunner isolation
defaults (network=none, cap_drop ALL, read-only root).

Each Attempt executes against a freshly jailed copy of its source, under a
workspace path bound to that Attempt's run/assignment/attempt/fence identity
(mirrors the legacy orchestrator's Workspace Jail in ``core/orchestrator.py``).
A consecutive Attempt — including a retry that reuses attempt_id with a higher
fence — always resolves to a distinct path and never reads a prior Attempt's
workspace (T-M4-ISOLATE-001). The jail is removed after use regardless of
outcome.

Same-host residual risk: this adapter still uses the host Docker API; process-
separated Worker agent remains the production-grade boundary.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from qarunner.adapters.docker_runner import DockerRunner
from qarunner.application.ports.worker_execution import (
    WorkerExecutionResult,
    WorkerExecutor,
)
from qarunner.core.paths import safe_subpath
from qarunner.domain.worker_execution import (
    CommitStartProof,
    require_execution_admission,
    sandbox_labels_for_proof,
    workspace_subpath_for_proof,
)
from qarunner.errors import RunnerError


class DockerWorkerExecutor(WorkerExecutor):
    """Admit pytest shards into labeled Docker sandboxes after commit-start."""

    def __init__(
        self,
        runner: DockerRunner | None = None,
        *,
        workspace_root: str = "./worker-workspaces",
    ) -> None:
        self._runner = runner if runner is not None else DockerRunner()
        self._workspace_root = workspace_root

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
        if not Path(cwd).is_dir():
            raise RunnerError(f"source workspace {cwd!r} does not exist")

        jail_dir = Path(safe_subpath(self._workspace_root, workspace_subpath_for_proof(admitted)))
        try:
            # Always start from a clean slate: a same-identity retry whose
            # prior jail survived a hard crash (cleanup below never ran) must
            # not leak that leftover state into this Attempt either.
            await asyncio.to_thread(shutil.rmtree, jail_dir, ignore_errors=True)
            await asyncio.to_thread(
                shutil.copytree, cwd, jail_dir, symlinks=True, ignore_dangling_symlinks=True
            )
        except OSError as exc:
            raise RunnerError(f"workspace jail creation failed: {exc}") from exc

        try:
            process = await self._runner.run(
                cmd=cmd,
                cwd=str(jail_dir),
                env=env,
                timeout=timeout,
                labels=labels,
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, jail_dir, ignore_errors=True)

        return WorkerExecutionResult(
            exit_code=process.exit_code,
            timed_out=process.timed_out,
            labels=labels,
        )
