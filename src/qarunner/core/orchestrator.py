"""Core orchestrator — creates and executes test runs via ports only."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import sys
from typing import TYPE_CHECKING

from qarunner.core.paths import safe_subpath
from qarunner.core.runners.base import BuildContext
from qarunner.errors import UnsafeArguments
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
    from qarunner.ports.store import Store

from qarunner.core.notification import build_run_card, send_feishu_card

logger = logging.getLogger(__name__)


# Pytest flags rejected from untrusted run requests. Most load arbitrary code /
# plugins / config files (argv-injection / RCE); the result-output flags
# (--junitxml / --alluredir) are platform-owned — a caller override would
# redirect result collection, breaking or forging a run's outcome.
_DANGEROUS_PYTEST_FLAGS = frozenset(
    {
        "-p",
        "--plugins",
        "-c",
        "--config-file",
        "--pyargs",
        "--rootdir",
        "--import-mode",
        "-o",
        "--override-ini",
        "--confcutdir",
        "--pythonpath",
        "--junitxml",
        "--junit-xml",
        "--alluredir",
    }
)


# Playwright CLI flags that load/execute arbitrary JS (a config or global
# setup/teardown module runs as Node code), or redirect platform-owned outputs.
# Reporter selection and the artifact output directory are owned by qarunner so
# JUnit collection and Playwright traces/screenshots/videos land under results/.
_DANGEROUS_PLAYWRIGHT_FLAGS = frozenset(
    {
        "-c",
        "--config",
        "--global-setup",
        "--global-teardown",
        "--output",
        "--reporter",
    }
)
_PLAYWRIGHT_TEST_SUFFIXES = (
    ".spec.ts",
    ".spec.js",
    ".spec.mjs",
    ".test.ts",
    ".test.js",
    ".test.mjs",
)


# Directory entries never copied into the workspace jail.
_JAIL_IGNORE_NAMES = frozenset({".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"})


def _compile_args(req: RunRequest, tests_dir: str, runner_name: str = "pytest") -> list[str]:
    """Compile a validated runner argv list from *req*.

    Rejects argv-injection vectors: dangerous flags supplied via ``args`` or
    ``extra_args``, and ``selected_files`` that escape the suite directory,
    start with ``-``, or are not valid for the selected runner. Raises
    ``UnsafeArguments`` (a ``UnsafePath`` subclass) on violation.
    """
    # -- playwright branch --
    if runner_name == "playwright":
        extra_tokens = shlex.split(req.extra_args) if req.extra_args.strip() else []
        for token in list(req.args) + extra_tokens:
            flag = token.split("=", 1)[0]
            if flag in _DANGEROUS_PLAYWRIGHT_FLAGS:
                raise UnsafeArguments(f"playwright flag {flag!r} is not allowed in run arguments")
        compiled: list[str] = list(req.args)
        if req.selected_markers:
            grep_expr = "|".join(
                f"@{re.escape(marker.lstrip('@'))}" for marker in req.selected_markers
            )
            compiled.extend(["--grep", grep_expr])
        compiled.extend(extra_tokens)
        for selected in req.selected_files:
            if selected.startswith("-"):
                raise UnsafeArguments(f"selected file {selected!r} must not start with '-'")
            safe_subpath(tests_dir, selected)
            if not selected.endswith(_PLAYWRIGHT_TEST_SUFFIXES):
                raise UnsafeArguments(f"selected file {selected!r} must be a Playwright test file")
            compiled.append(selected)
        return compiled

    extra_tokens = shlex.split(req.extra_args) if req.extra_args.strip() else []

    for token in list(req.args) + extra_tokens:
        # Normalise "--rootdir=/x" / "-o key=val" to the bare flag before matching.
        flag = token.split("=", 1)[0]
        if flag in _DANGEROUS_PYTEST_FLAGS:
            raise UnsafeArguments(f"pytest flag {flag!r} is not allowed in run arguments")

    compiled: list[str] = list(req.args)
    if req.selected_markers:
        marker_expr = " or ".join(req.selected_markers)
        compiled.extend(["-m", marker_expr])
    compiled.extend(extra_tokens)
    for selected in req.selected_files:
        if selected.startswith("-"):
            raise UnsafeArguments(f"selected file {selected!r} must not start with '-'")
        # Allow pytest node ids ("file.py::test_x"); validate only the path part.
        path_part = selected.split("::", 1)[0]
        safe_subpath(tests_dir, path_part)  # raises UnsafePath if it escapes the suite
        if not path_part.endswith(".py"):
            raise UnsafeArguments(f"selected file {selected!r} must be a .py file")
        compiled.append(selected)
    return compiled


# Environment-variable names (exact) and prefixes that let a child process
# hijack dynamic-library loading, the Python import path, command resolution,
# or Node's module loader — classic sandbox-escape / code-injection vectors.
# Rejected from caller-supplied ``env`` so untrusted test code can't, for
# example, set LD_PRELOAD to load arbitrary native code, or NODE_OPTIONS
# (--require) to run arbitrary JS in the playwright runner (FUNC-1 + SEC-3 H-3).
_DANGEROUS_ENV_PREFIXES = ("LD_", "DYLD_", "PYTHON")
_DANGEROUS_ENV_NAMES = frozenset({"PATH", "BASH_ENV", "NODE_OPTIONS"})


def _sanitize_env(env: dict[str, str]) -> dict[str, str]:
    """Validate a caller-supplied environment mapping (FUNC-1).

    Rejects names that could alter dynamic-linker / interpreter behaviour or
    command resolution (``LD_*``/``DYLD_*``/``PYTHON*``/``PATH``/``BASH_ENV``,
    matched case-insensitively), plus malformed entries (empty name, ``=`` or
    NUL in a name, NUL in a value) that would otherwise fail at the subprocess
    boundary. Raises ``UnsafeArguments`` (→ HTTP 400) on violation; returns a
    shallow copy when safe.
    """
    for key, value in env.items():
        if not key or "=" in key or "\x00" in key:
            raise UnsafeArguments(f"invalid environment variable name {key!r}")
        if "\x00" in value:
            raise UnsafeArguments(f"invalid value for environment variable {key!r}")
        upper = key.upper()
        if upper in _DANGEROUS_ENV_NAMES or upper.startswith(_DANGEROUS_ENV_PREFIXES):
            raise UnsafeArguments(
                f"environment variable {key!r} is not allowed (injection vector)"
            )
    return dict(env)


class RunOrchestrator:
    """Coordinates run creation and execution through pure port interactions."""

    def __init__(
        self,
        *,
        registry: RunnerRegistry,
        store: Store,
        scheduler: TaskScheduler,
        process: ProcessRunner,
        collector: ResultCollector,
        reporter: AllureReporter,
        clock: Clock,
        ids: IdGenerator,
        tests_root: str,
        artifacts_root: str,
        executable: str,
        default_timeout: int = 1800,
        worker_node_id: str = "default-node",
        public_url: str = "",
    ) -> None:
        self._registry = registry
        self._store = store
        self._scheduler = scheduler
        self._process = process
        self._collector = collector
        self._reporter = reporter
        self._clock = clock
        self._ids = ids
        self._tests_root = tests_root
        self._artifacts_root = artifacts_root
        self._executable = executable or sys.executable
        self._default_timeout = default_timeout
        self._worker_node_id = worker_node_id
        self._public_url = public_url

    # ── create ────────────────────────────────────────────────────────────

    async def create(
        self, req: RunRequest, created_by: str = "system", profile_id: str | None = None
    ) -> Run:
        """Create a new run, persist it, and schedule background execution."""
        # 1. Validate runner
        runner = self._registry.get(req.runner)  # raises UnknownRunner

        # 2. Validate path
        tests_dir = safe_subpath(self._tests_root, req.tests_path)  # raises UnsafePath

        # 3. Build and persist Run (args validated against argv-injection)
        run_id = self._ids.new_id()
        now = self._clock.now()

        compiled_args = _compile_args(req, tests_dir, runner_name=runner.name)
        safe_env = _sanitize_env(req.env)

        run = Run(
            id=run_id,
            status=RunStatus.QUEUED,
            runner=runner.name,
            created_by=created_by,
            tests_path=req.tests_path,
            args=compiled_args,
            allure_enabled=req.allure,
            timeout=req.timeout,
            executor_mode="docker",
            created_at=now,
            env=safe_env,
            worker_node_id=self._worker_node_id,
            profile_id=profile_id,
        )
        await self._store.save(run)

        # 4. Signal the persistent scheduler (poller picks up from DB/store)
        self._scheduler.enqueue(run_id)

        return run

    # ── execute ───────────────────────────────────────────────────────────

    async def execute(self, run_id: str) -> None:
        """Background task: run tests, collect results, persist outcome.

        Top-level try/except guarantees the run never stays in RUNNING.
        """
        run = await self._store.get(run_id)
        # Guard: if already in a terminal state (cancelled between dequeue
        # and this task starting), stop early — nothing to execute.
        if run.status in (
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.COMPLETED,
            RunStatus.TIMEOUT,
        ):
            logger.debug("execute(%s): run already terminal (%s), skipping", run_id, run.status)
            return

        runner = self._registry.get(run.runner)

        try:
            # 1. Mark RUNNING
            now = self._clock.now()
            run = _replace(run, status=RunStatus.RUNNING, started_at=now)
            await self._store.save(run)

            # 2. Build command — results_dir must be absolute per plan
            from pathlib import Path

            results_dir = str((Path(self._artifacts_root) / run.id / "results").resolve())
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
                    resolved_dir = Path(dirpath).resolve()
                    artifacts = Path(self._artifacts_root).resolve()
                    run_resolved = run_dir.resolve()
                    to_ignore = []
                    for name in contents:
                        resolved_child = (resolved_dir / name).resolve()
                        if (
                            resolved_child in (artifacts, run_resolved)
                            or name in _JAIL_IGNORE_NAMES
                        ):
                            to_ignore.append(name)
                    return to_ignore

                if Path(tests_dir).exists():
                    await asyncio.to_thread(
                        shutil.copytree,
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
                    logger.warning(
                        "Source tests_dir %s does not exist; running without jail.", tests_dir
                    )
            except Exception as e:
                logger.warning(
                    "Failed to create Workspace Jail at %s (%r); running directly in %s.",
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

            # 3. Execute process (always Docker — subprocess path removed)
            timeout = run.timeout or self._default_timeout
            runner_to_use = self._process

            # playwright: JUnit output via env var, not CLI flag
            proc_env = dict(run.env)
            if runner.name == "playwright":
                proc_env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] = f"{results_dir}/junit.xml"

            proc: ProcessResult = await runner_to_use.run(
                cmd,
                cwd=exec_cwd,
                env=proc_env,
                timeout=timeout,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
            run = _replace(run, exit_code=proc.exit_code)

            # 4. Collect results (always)
            collected: CollectResult | None = await asyncio.to_thread(
                self._collector.collect, results_dir
            )

            # 4b. Persist per-case results for cross-run analysis (baseline diff /
            # flaky / case history). The collector already parses them; until now
            # only the summary was kept and `cases` was dropped. Keyed by
            # run.created_at (stable sort key) and tests_path (so case history is
            # queryable without joining runs).
            if collected is not None:
                await self._store.save_cases(
                    run.id, run.tests_path, run.created_at, collected.cases
                )

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

            # Fire-and-forget notification: never blocks or fails the run.
            asyncio.create_task(self._notify(run))

        except asyncio.CancelledError:
            # User-initiated cancel (or a shutdown drain past its deadline):
            # CancelledError is a BaseException, so the ``except Exception`` below
            # never sees it — without this branch the run would be stranded in
            # RUNNING. Persist a terminal CANCELLED state, then re-raise to keep
            # cancellation semantics intact (the runner's own finally already
            # killed the subprocess / container).
            try:
                run = _replace(
                    run,
                    status=RunStatus.CANCELLED,
                    finished_at=self._clock.now(),
                )
                await self._store.save(run)
            except Exception:  # pragma: no cover — needs real asyncio task cancellation
                logger.exception("failed to persist cancelled state for run %s", run_id)
            raise
        except Exception as exc:
            # API-facing error: the exception's repr (type + message) only,
            # truncated. The full traceback goes to the server log via
            # logger.exception below — never into run.error, because stack frames
            # leak absolute source paths and code structure to API clients
            # (defence in depth for a platform that executes untrusted test code).
            error_msg = repr(exc)[:2000]
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
                    await asyncio.to_thread(shutil.rmtree, jail_dir, ignore_errors=True)
                    logger.info("Workspace Jail cleaned up at %s", jail_dir)
                except Exception as clean_exc:
                    logger.warning(
                        "Failed to cleanup Workspace Jail at %s: %r", jail_dir, clean_exc
                    )

    async def _notify(self, run: Run) -> None:
        """Send a Feishu card notification for *run* if its profile has a webhook.

        Fire-and-forget via ``asyncio.create_task`` — failures are logged but
        never propagated.
        """
        try:
            if not self._public_url or not run.profile_id:
                return
            profile = await self._store.get_profile(run.profile_id)
            if profile is None or not profile.webhook_url:
                return

            allure_url = f"{self._public_url}/runs/{run.id}/report"
            card = build_run_card(run, allure_url)
            await send_feishu_card(profile.webhook_url, card)
        except Exception:
            logger.exception("Notification failed for run %s", run.id)

    async def cancel(self, run_id: str) -> Run:
        """Cancel a queued or running run.

        Cancels the background task (propagating ``CancelledError`` into the
        execution, whose runner finally kills the subprocess / container), then
        re-reads and — if still in flight — persists a terminal CANCELLED state.
        A run that finished on its own in the meantime is returned unchanged, so
        a real terminal status is never clobbered. Raises ``RunNotFound`` if the
        id is unknown.
        """
        self._scheduler.cancel(run_id)
        run = await self._store.get(run_id)
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            run = _replace(
                run,
                status=RunStatus.CANCELLED,
                finished_at=self._clock.now(),
            )
            await self._store.save(run)
        return run

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def drain(self, timeout: float | None = None) -> None:
        """Wait for in-flight background executions to settle (DATA-4).

        Delegates to the task scheduler so a graceful shutdown lets running
        executions persist their terminal state before the store closes.
        """
        await self._scheduler.drain(timeout)


def _replace(run: Run, **kwargs: object) -> Run:
    """Return a copy of *run* with fields replaced (frozen model)."""
    return run.model_copy(update=kwargs)
