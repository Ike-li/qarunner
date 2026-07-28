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
    ResidualSandboxObservation,
    require_execution_admission,
    sandbox_labels_for_proof,
    workspace_subpath_for_proof,
)
from qarunner.errors import RunnerError

# Label keys stamped by sandbox_labels_for_proof — used both for create and
# for post-restart residual listing (T-M4-RESTART-001).
_LABEL_RUN = "qarunner.run_id"
_LABEL_ASSIGNMENT = "qarunner.assignment_id"
_LABEL_ATTEMPT = "qarunner.attempt_id"
_LABEL_FENCE = "qarunner.fence"
_LABEL_WORKER = "qarunner.worker_id"
_LABEL_GENERATION = "qarunner.worker_generation"
_LABEL_COMMIT = "qarunner.start_commit_key"


def list_residual_sandbox_observations(
    client,
    *,
    worker_id: str | None = None,
) -> tuple[ResidualSandboxObservation, ...]:
    """List residual M4 sandboxes via attempt/fence labels (T-M4-RESTART-001).

    Pure observation helper: never starts, stops, or removes containers. The
    caller feeds the result into :func:`reconcile_residual_sandboxes` together
    with the control-plane expected-live set. Filters to containers that carry
    the full M4 label set; optionally scopes to one ``worker_id``.
    """
    filters: dict[str, list[str]] = {"label": [_LABEL_ATTEMPT]}
    if worker_id is not None:
        filters["label"] = [_LABEL_ATTEMPT, f"{_LABEL_WORKER}={worker_id}"]
    containers = client.containers.list(all=True, filters=filters)
    observations: list[ResidualSandboxObservation] = []
    for container in containers:
        labels = getattr(container, "labels", None) or {}
        required = (
            _LABEL_RUN,
            _LABEL_ASSIGNMENT,
            _LABEL_ATTEMPT,
            _LABEL_FENCE,
            _LABEL_WORKER,
            _LABEL_GENERATION,
            _LABEL_COMMIT,
        )
        if any(key not in labels for key in required):
            continue
        try:
            fence = int(labels[_LABEL_FENCE])
            generation = int(labels[_LABEL_GENERATION])
        except (TypeError, ValueError):
            continue
        status = getattr(container, "status", "") or ""
        observations.append(
            ResidualSandboxObservation(
                container_id=str(container.id),
                run_id=labels[_LABEL_RUN],
                assignment_id=labels[_LABEL_ASSIGNMENT],
                attempt_id=labels[_LABEL_ATTEMPT],
                fence=fence,
                worker_id=labels[_LABEL_WORKER],
                worker_generation=generation,
                start_commit_key=labels[_LABEL_COMMIT],
                running=status == "running",
            )
        )
    return tuple(
        sorted(
            observations,
            key=lambda o: (o.run_id, o.assignment_id, o.attempt_id, o.fence, o.container_id),
        )
    )


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
