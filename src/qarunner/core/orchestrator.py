"""Core orchestrator — creates and executes test runs via ports only."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from qarunner.core.runners.base import BuildContext
from qarunner.models import (
    CollectResult,
    ProcessResult,
    ReportRef,
    Run,
    RunRequest,
    RunStatus,
)

if TYPE_CHECKING:
    from qarunner.core.runners.registry import RunnerRegistry
    from qarunner.ports.clock import Clock
    from qarunner.ports.collector import ResultCollector
    from qarunner.ports.ids import IdGenerator
    from qarunner.ports.process import ProcessRunner
    from qarunner.ports.reporter import AllureReporter
    from qarunner.ports.scheduler import TaskScheduler
    from qarunner.ports.store import RunStore

logger = logging.getLogger(__name__)


class RunOrchestrator:
    """Coordinates run creation and execution through pure port interactions."""

    def __init__(
        self,
        *,
        registry: RunnerRegistry,
        store: RunStore,
        scheduler: TaskScheduler,
        process: ProcessRunner,
        collector: ResultCollector,
        reporter: AllureReporter,
        clock: Clock,
        ids: IdGenerator,
        tests_root: str,
        artifacts_root: str,
        executable: str,
        process_docker: ProcessRunner | None = None,
        default_timeout: int = 1800,
    ) -> None:
        self._registry = registry
        self._store = store
        self._scheduler = scheduler
        self._process = process
        self._process_docker = process_docker
        self._collector = collector
        self._reporter = reporter
        self._clock = clock
        self._ids = ids
        self._tests_root = tests_root
        self._artifacts_root = artifacts_root
        self._executable = executable or sys.executable
        self._default_timeout = default_timeout

    # ── create ────────────────────────────────────────────────────────────

    async def create(self, req: RunRequest, created_by: str = "system") -> Run:
        """Create a new run, persist it, and schedule background execution."""
        # 1. Validate runner
        runner = self._registry.get(req.runner)  # raises UnknownRunner

        # 2. Validate path
        from qarunner.core.paths import safe_subpath

        safe_subpath(self._tests_root, req.tests_path)  # raises UnsafePath

        # 3. Build and persist Run
        run_id = self._ids.new_id()
        now = self._clock.now()

        compiled_args = list(req.args)
        if req.selected_markers:
            marker_expr = " or ".join(req.selected_markers)
            compiled_args.extend(["-m", marker_expr])
        if req.extra_args and req.extra_args.strip():
            import shlex
            compiled_args.extend(shlex.split(req.extra_args))
        if req.selected_files:
            compiled_args.extend(req.selected_files)

        run = Run(
            id=run_id,
            status=RunStatus.QUEUED,
            runner=runner.name,
            created_by=created_by,
            tests_path=req.tests_path,
            args=compiled_args,
            allure_enabled=req.allure,
            timeout=req.timeout,
            executor_mode=req.executor_mode,
            created_at=now,
        )
        await self._store.save(run)

        # 4. Schedule background execution
        self._scheduler.schedule(self.execute(run_id))

        return run

    # ── execute ───────────────────────────────────────────────────────────

    async def execute(self, run_id: str) -> None:
        """Background task: run tests, collect results, persist outcome.

        Top-level try/except guarantees the run never stays in RUNNING.
        """
        run = await self._store.get(run_id)
        runner = self._registry.get(run.runner)

        try:
            # 1. Mark RUNNING
            now = self._clock.now()
            run = _replace(run, status=RunStatus.RUNNING, started_at=now)
            await self._store.save(run)

            # 2. Build command — results_dir must be absolute per plan
            from pathlib import Path

            results_dir = str(
                (Path(self._artifacts_root) / run.id / "results").resolve()
            )
            from qarunner.core.paths import safe_subpath

            tests_dir = safe_subpath(self._tests_root, run.tests_path)

            run_dir = Path(self._artifacts_root) / run.id
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                run_dir = Path("./artifacts") / run.id
                run_dir.mkdir(parents=True, exist_ok=True)
            stdout_file = str(run_dir / "stdout.log")
            stderr_file = str(run_dir / "stderr.log")

            # Workspace Jail Isolation (Sandbox)
            exec_cwd = tests_dir
            jail_dir = run_dir / "workspace"
            jail_created = False

            try:
                import shutil

                def ignore_patterns(dirpath: str, contents: list[str]) -> list[str]:
                    to_ignore = []
                    resolved_dir = Path(dirpath).resolve()
                    for name in contents:
                        resolved_child = (resolved_dir / name).resolve()
                        if resolved_child == Path(self._artifacts_root).resolve():
                            to_ignore.append(name)
                        elif resolved_child == run_dir.resolve():
                            to_ignore.append(name)
                        elif name in {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"}:
                            to_ignore.append(name)
                    return to_ignore

                if Path(tests_dir).exists():
                    shutil.copytree(
                        tests_dir,
                        jail_dir,
                        symlinks=True,
                        ignore_dangling_symlinks=True,
                        ignore=ignore_patterns,
                        dirs_exist_ok=True,
                    )
                    exec_cwd = str(jail_dir)
                    jail_created = True
                    logger.info("Workspace Jail successfully created at %s", exec_cwd)
                else:
                    logger.warning("Source tests_dir %s does not exist. Falling back without Workspace Jail.", tests_dir)
            except Exception as e:
                logger.warning(
                    "Failed to create Workspace Jail at %s (error: %r). Falling back to direct execution in %s.",
                    jail_dir,
                    e,
                    tests_dir,
                )

            ctx = BuildContext(
                tests_dir=exec_cwd,
                results_dir=results_dir,
                executable=self._executable,
                args=run.args,
            )
            cmd = runner.build_command(ctx)

            # 3. Execute process
            timeout = run.timeout or self._default_timeout
            runner_to_use = self._process
            if run.executor_mode == "docker" and self._process_docker is not None:
                runner_to_use = self._process_docker

            proc: ProcessResult = await runner_to_use.run(
                cmd,
                cwd=exec_cwd,
                timeout=timeout,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
            run = _replace(run, exit_code=proc.exit_code)


            # 4. Collect results (always)
            collected: CollectResult | None = self._collector.collect(results_dir)

            # 5. Generate report (never let failures propagate)
            report: ReportRef = await self._reporter.generate(
                results_dir, enabled=run.allure_enabled
            )

            # 6. Determine terminal status
            if proc.timed_out:
                status = RunStatus.TIMEOUT
            elif collected is not None:
                status = RunStatus.COMPLETED
            else:
                status = RunStatus.FAILED

            # 7. Persist final state
            run = _replace(
                run,
                status=status,
                summary=collected.summary if collected else None,
                report=report,
                finished_at=self._clock.now(),
            )
            await self._store.save(run)

        except Exception as exc:
            # Error model: repr(exception) + stderr tail, truncated to ~2000 chars
            import traceback

            parts = [repr(exc)]
            # Include traceback tail for debugging
            tb = traceback.format_exc()
            if len(tb) > 500:
                parts.append(tb[-500:])
            error_msg = "\n".join(parts)[:2000]
            logger.exception("execute failed for run %s", run_id)
            try:
                run = _replace(
                    run,
                    status=RunStatus.FAILED,
                    error=error_msg,
                    finished_at=self._clock.now(),
                )
                await self._store.save(run)
            except Exception:
                logger.exception("failed to persist error state for run %s", run_id)
        finally:
            if "jail_created" in locals() and jail_created:
                try:
                    import shutil
                    shutil.rmtree(jail_dir, ignore_errors=True)
                    logger.info("Workspace Jail cleaned up at %s", jail_dir)
                except Exception as clean_exc:
                    logger.warning("Failed to cleanup Workspace Jail at %s: %r", jail_dir, clean_exc)


def _replace(run: Run, **kwargs: object) -> Run:
    """Return a copy of *run* with fields replaced (frozen model)."""
    return run.model_copy(update=kwargs)
