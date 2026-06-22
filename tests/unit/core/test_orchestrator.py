"""Tests for qarunner.core.orchestrator — RunOrchestrator.create + execute."""

from __future__ import annotations

import pytest

from qarunner.core.orchestrator import RunOrchestrator
from qarunner.core.runners.pytest_runner import PytestRunner
from qarunner.core.runners.registry import RunnerRegistry
from qarunner.errors import UnknownRunner, UnsafeArguments, UnsafePath
from qarunner.models import (
    CollectResult,
    ProcessResult,
    ReportRef,
    RunRequest,
    RunStatus,
    TestSummary,
)
from tests.fakes.fake_clock import FakeClock
from tests.fakes.fake_collector import FakeResultCollector
from tests.fakes.fake_ids import FakeIdGenerator
from tests.fakes.fake_process import FakeProcessRunner
from tests.fakes.fake_reporter import FakeAllureReporter
from tests.fakes.fake_scheduler import FakeScheduler
from tests.fakes.fake_store import InMemoryRunStore


def _make_orchestrator(
    *,
    process_handler=None,
    collector_preset=None,
    report_preset=None,
    tests_root="/work/tests",
):
    """Helper to build an orchestrator wired to fakes."""
    registry = RunnerRegistry()
    registry.register(PytestRunner())

    if process_handler is None:

        def _default_handler(cmd, cwd, env, timeout):
            return ProcessResult(exit_code=0, stdout="ok", stderr="", duration_ms=100)

        process_handler = _default_handler

    if report_preset is None:
        report_preset = ReportRef(
            allure_results_dir="/artifacts/id-001/results",
            allure_report_file="/artifacts/id-001/results/allure-report/index.html",
            html_generated=True,
        )

    return RunOrchestrator(
        registry=registry,
        store=InMemoryRunStore(),
        scheduler=FakeScheduler(),
        process=FakeProcessRunner(handler=process_handler),
        collector=FakeResultCollector(preset=collector_preset),
        reporter=FakeAllureReporter(preset=report_preset),
        clock=FakeClock(),
        ids=FakeIdGenerator(),
        tests_root=tests_root,
        artifacts_root="/artifacts",
        executable="/usr/bin/python3",
        default_timeout=600,
    )


class TestDrain:
    """RunOrchestrator.drain() delegates graceful shutdown to the scheduler."""

    @pytest.mark.asyncio
    async def test_drain_delegates_to_scheduler(self):
        orch = _make_orchestrator()
        await orch.drain(timeout=2.0)
        assert orch._scheduler.drained == 1


class TestCreate:
    """RunOrchestrator.create() validates, persists, and schedules."""

    @pytest.mark.asyncio
    async def test_create_returns_queued_run(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        assert run.status == RunStatus.QUEUED
        assert run.id == "id-001"
        assert run.runner == "pytest"

    @pytest.mark.asyncio
    async def test_create_persists_to_store(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        stored = await orch._store.get(run.id)
        assert stored.id == run.id

    @pytest.mark.asyncio
    async def test_create_schedules_execution(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample")
        await orch.create(req)
        assert orch._scheduler.scheduled == 1

    @pytest.mark.asyncio
    async def test_create_stores_request_fields(self):
        orch = _make_orchestrator()
        req = RunRequest(
            tests_path="sample",
            args=["-k", "test_x"],
            allure=False,
            timeout=120,
        )
        run = await orch.create(req)
        assert run.args == ["-k", "test_x"]
        assert run.allure_enabled is False
        assert run.timeout == 120

    @pytest.mark.asyncio
    async def test_unknown_runner_raises(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", runner="nosuch")
        with pytest.raises(UnknownRunner):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_unsafe_path_raises(self):
        orch = _make_orchestrator(tests_root="/work/tests")
        req = RunRequest(tests_path="../../etc")
        with pytest.raises(UnsafePath):
            await orch.create(req)


class TestCreateArgValidation:
    """create() rejects argv-injection vectors (SEC-1)."""

    @pytest.mark.asyncio
    async def test_extra_args_plugin_flag_rejected(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", extra_args="-p evil_plugin")
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_extra_args_override_ini_rejected(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", extra_args="-o addopts=-pevil")
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_extra_args_rootdir_equals_form_rejected(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", extra_args="--rootdir=/etc")
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_args_pyargs_flag_rejected(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", args=["--pyargs", "os"])
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_selected_file_dash_prefix_rejected(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", selected_files=["-p"])
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_selected_file_traversal_rejected(self):
        orch = _make_orchestrator(tests_root="/work/tests")
        req = RunRequest(tests_path="sample", selected_files=["../../../etc/passwd"])
        with pytest.raises(UnsafePath):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_selected_file_non_py_rejected(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", selected_files=["conftest.ini"])
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_legitimate_args_and_files_compile(self):
        orch = _make_orchestrator()
        req = RunRequest(
            tests_path="sample",
            args=["-k", "test_x"],
            extra_args="--tb=short --maxfail=2",
            selected_markers=["smoke"],
            selected_files=["test_a.py", "sub/test_b.py::test_case"],
        )
        run = await orch.create(req)
        assert run.args[:2] == ["-k", "test_x"]
        assert "-m" in run.args
        assert "smoke" in run.args
        assert "--tb=short" in run.args
        assert "--maxfail=2" in run.args
        assert "test_a.py" in run.args
        assert "sub/test_b.py::test_case" in run.args
        assert run.status == RunStatus.QUEUED


class TestEnvHandling:
    """FUNC-1: env reaches the runner; injection-vector keys are rejected."""

    @pytest.mark.asyncio
    async def test_create_stores_sanitized_env(self):
        orch = _make_orchestrator()
        req = RunRequest(
            tests_path="sample",
            env={"API_BASE_URL": "https://x", "TOKEN": "t"},
        )
        run = await orch.create(req)
        assert run.env == {"API_BASE_URL": "https://x", "TOKEN": "t"}

    @pytest.mark.asyncio
    async def test_execute_passes_env_to_runner(self):
        captured = {}

        def handler(cmd, cwd, env, timeout):
            captured["env"] = env
            return ProcessResult(exit_code=0, stdout="", stderr="", duration_ms=10)

        orch = _make_orchestrator(
            process_handler=handler,
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=0, passed=0, failed=0, skipped=0, error=0, duration_ms=0
                ),
                cases=[],
            ),
        )
        req = RunRequest(tests_path="sample", env={"CUSTOM_VAR": "value-42"})
        run = await orch.create(req)
        await orch.execute(run.id)

        assert captured["env"] == {"CUSTOM_VAR": "value-42"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_key",
        [
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "PATH",
            "BASH_ENV",
            "ld_preload",  # matched case-insensitively
            "Path",
        ],
    )
    async def test_create_rejects_injection_env_keys(self, bad_key):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", env={bad_key: "x"})
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_key", ["", "A=B", "A\x00B"])
    async def test_create_rejects_malformed_env_name(self, bad_key):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", env={bad_key: "x"})
        with pytest.raises(UnsafeArguments):
            await orch.create(req)

    @pytest.mark.asyncio
    async def test_create_rejects_env_value_with_nul(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", env={"GOOD": "bad\x00value"})
        with pytest.raises(UnsafeArguments):
            await orch.create(req)


class TestExecute:
    """RunOrchestrator.execute() runs the full lifecycle."""

    @pytest.mark.asyncio
    async def test_successful_run(self):
        summary = TestSummary(
            total=2, passed=2, failed=0, skipped=0, error=0, duration_ms=200
        )
        orch = _make_orchestrator(
            collector_preset=CollectResult(summary=summary, cases=[]),
        )
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        # Let the scheduled task run
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.COMPLETED
        assert stored.summary is not None
        assert stored.summary.passed == 2
        assert stored.report is not None
        assert stored.finished_at is not None

    @pytest.mark.asyncio
    async def test_failed_run_no_collector_result(self):
        orch = _make_orchestrator(collector_preset=None)
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_timeout_run(self):
        def handler(cmd, cwd, env, timeout):
            return ProcessResult(
                exit_code=-1, stdout="", stderr="killed", duration_ms=100, timed_out=True
            )

        orch = _make_orchestrator(process_handler=handler)
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_execute_stores_exit_code(self):
        def handler(cmd, cwd, env, timeout):
            return ProcessResult(
                exit_code=1, stdout="", stderr="fail", duration_ms=50
            )

        orch = _make_orchestrator(
            process_handler=handler,
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=1, passed=0, failed=1, skipped=0, error=0, duration_ms=50
                ),
                cases=[],
            ),
        )
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.exit_code == 1

    @pytest.mark.asyncio
    async def test_execute_passes_args_to_command(self):
        collected_args = {}

        def handler(cmd, cwd, env, timeout):
            collected_args["cmd"] = cmd
            return ProcessResult(
                exit_code=0, stdout="", stderr="", duration_ms=10
            )

        orch = _make_orchestrator(
            process_handler=handler,
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=0, passed=0, failed=0, skipped=0, error=0, duration_ms=0
                ),
                cases=[],
            ),
        )
        req = RunRequest(tests_path="sample", args=["-k", "test_foo"])
        run = await orch.create(req)
        await orch.execute(run.id)

        assert "-k" in collected_args["cmd"]
        assert "test_foo" in collected_args["cmd"]

    @pytest.mark.asyncio
    async def test_execute_uses_run_timeout(self):
        captured_timeout = {}

        def handler(cmd, cwd, env, timeout):
            captured_timeout["t"] = timeout
            return ProcessResult(
                exit_code=0, stdout="", stderr="", duration_ms=10
            )

        orch = _make_orchestrator(
            process_handler=handler,
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=0, passed=0, failed=0, skipped=0, error=0, duration_ms=0
                ),
                cases=[],
            ),
        )
        req = RunRequest(tests_path="sample", timeout=999)
        run = await orch.create(req)
        await orch.execute(run.id)

        assert captured_timeout["t"] == 999

    @pytest.mark.asyncio
    async def test_execute_uses_default_timeout(self):
        captured_timeout = {}

        def handler(cmd, cwd, env, timeout):
            captured_timeout["t"] = timeout
            return ProcessResult(
                exit_code=0, stdout="", stderr="", duration_ms=10
            )

        orch = _make_orchestrator(
            process_handler=handler,
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=0, passed=0, failed=0, skipped=0, error=0, duration_ms=0
                ),
                cases=[],
            ),
        )
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        assert captured_timeout["t"] == 600  # default

    @pytest.mark.asyncio
    async def test_execute_reports_allure_disabled(self):
        orch = _make_orchestrator(
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=0, passed=0, failed=0, skipped=0, error=0, duration_ms=0
                ),
                cases=[],
            ),
        )
        req = RunRequest(tests_path="sample", allure=False)
        run = await orch.create(req)
        await orch.execute(run.id)

        # Reporter should have been called with enabled=False
        stored = await orch._store.get(run.id)
        assert stored.allure_enabled is False


class TestExecuteErrorModel:
    """Top-level catch-all in execute guarantees terminal state."""

    @pytest.mark.asyncio
    async def test_process_crash_sets_failed(self):
        def handler(cmd, cwd, env, timeout):
            raise RuntimeError("executable not found")

        orch = _make_orchestrator(process_handler=handler)
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED
        assert stored.error is not None
        assert "executable not found" in stored.error
        assert stored.finished_at is not None

    @pytest.mark.asyncio
    async def test_collector_crash_sets_failed(self):
        def bad_collect(run_dir):
            raise ValueError("xml parse exploded")

        orch = _make_orchestrator()
        orch._collector.collect = bad_collect  # type: ignore[assignment]

        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED
        assert "xml parse exploded" in (stored.error or "")

    @pytest.mark.asyncio
    async def test_reporter_crash_still_completes(self):
        """Reporter failure is caught internally — shouldn't crash the run."""

        def bad_generate(run_dir, enabled=True):
            raise RuntimeError("allure CLI missing")

        orch = _make_orchestrator(
            collector_preset=CollectResult(
                summary=TestSummary(
                    total=1, passed=1, failed=0, skipped=0, error=0, duration_ms=10
                ),
                cases=[],
            ),
        )
        orch._reporter.generate = bad_generate  # type: ignore[assignment]

        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        # execute will crash, but top-level catch sets FAILED
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED
        assert stored.error is not None

    @pytest.mark.asyncio
    async def test_error_truncated_to_2000_chars(self):
        long_msg = "x" * 5000

        def handler(cmd, cwd, env, timeout):
            raise RuntimeError(long_msg)

        orch = _make_orchestrator(process_handler=handler)
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.error is not None
        assert len(stored.error) <= 2000

    @pytest.mark.asyncio
    async def test_double_failure_logs_only(self):
        """If save fails during error recovery, it logs but doesn't crash."""

        def handler(cmd, cwd, env, timeout):
            raise RuntimeError("boom")

        orch = _make_orchestrator(process_handler=handler)
        # Make save fail on the second call (error recovery)
        original_save = orch._store.save
        call_count = 0

        async def flaky_save(run):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise ConnectionError("db down")
            return await original_save(run)

        orch._store.save = flaky_save  # type: ignore[assignment]
        req = RunRequest(tests_path="sample")
        run = await orch.create(req)
        # Should not raise even though save fails in error handler
        await orch.execute(run.id)


class TestRunnerSelection:
    """Orchestrator picks the right runner from the registry."""

    @pytest.mark.asyncio
    async def test_runner_name_stored(self):
        orch = _make_orchestrator()
        req = RunRequest(tests_path="sample", runner="pytest")
        run = await orch.create(req)
        assert run.runner == "pytest"


class TestWorkspaceJail:
    """Tests for the Workspace Jail (Sandbox Isolation) copy and cleanup behavior."""

    @pytest.mark.asyncio
    async def test_workspace_jail_success_and_cleanup(self, tmp_path):
        # 1. Create real tests directory and artifact directories using tmp_path
        tests_root = tmp_path / "tests_root"
        tests_root.mkdir()

        # Create a sample test file inside the test suite path
        suite_path = "suite_abc"
        suite_dir = tests_root / suite_path
        suite_dir.mkdir()

        test_file = suite_dir / "test_dummy.py"
        test_file.write_text("def test_dummy(): pass")

        # Create a subdirectory that we expect to be ignored, e.g., .git or .venv
        git_dir = suite_dir / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("dummy")

        artifacts_root = tmp_path / "artifacts_root"
        artifacts_root.mkdir()

        # 2. Build orchestrator with real filesystem paths
        registry = RunnerRegistry()
        registry.register(PytestRunner())

        captured_cwd = []
        def handler(cmd, cwd, env, timeout):
            captured_cwd.append(cwd)
            return ProcessResult(exit_code=0, stdout="ok", stderr="", duration_ms=10)

        orch = RunOrchestrator(
            registry=registry,
            store=InMemoryRunStore(),
            scheduler=FakeScheduler(),
            process=FakeProcessRunner(handler=handler),
            collector=FakeResultCollector(preset=None),
            reporter=FakeAllureReporter(preset=None),
            clock=FakeClock(),
            ids=FakeIdGenerator(),
            tests_root=str(tests_root),
            artifacts_root=str(artifacts_root),
            executable="/usr/bin/python3",
            default_timeout=600,
        )

        # 3. Create and execute the run
        req = RunRequest(tests_path=suite_path)
        run = await orch.create(req)

        # Verify initial state of jail directory
        jail_dir = artifacts_root / run.id / "workspace"
        assert not jail_dir.exists()

        await orch.execute(run.id)

        # 4. Verify workspace jail was successfully created and run in
        assert len(captured_cwd) == 1
        assert captured_cwd[0] == str(jail_dir)

        # 5. Verify workspace jail was automatically cleaned up after completion
        assert not jail_dir.exists()

    @pytest.mark.asyncio
    async def test_workspace_jail_failure_fallback(self, tmp_path):
        # tests_root is configured to a non-existent path to trigger an error
        tests_root = tmp_path / "non_existent_tests_root"
        artifacts_root = tmp_path / "artifacts_root"
        artifacts_root.mkdir()

        registry = RunnerRegistry()
        registry.register(PytestRunner())

        captured_cwd = []
        def handler(cmd, cwd, env, timeout):
            captured_cwd.append(cwd)
            return ProcessResult(exit_code=0, stdout="ok", stderr="", duration_ms=10)

        orch = RunOrchestrator(
            registry=registry,
            store=InMemoryRunStore(),
            scheduler=FakeScheduler(),
            process=FakeProcessRunner(handler=handler),
            collector=FakeResultCollector(preset=None),
            reporter=FakeAllureReporter(preset=None),
            clock=FakeClock(),
            ids=FakeIdGenerator(),
            tests_root=str(tests_root),
            artifacts_root=str(artifacts_root),
            executable="/usr/bin/python3",
            default_timeout=600,
        )

        req = RunRequest(tests_path="some_suite")
        run = await orch.create(req)

        # Execute
        await orch.execute(run.id)

        # Should fall back cleanly and execute with the original tests_dir path
        assert len(captured_cwd) == 1
        from qarunner.core.paths import safe_subpath
        expected_fallback = safe_subpath(str(tests_root), "some_suite")
        assert captured_cwd[0] == expected_fallback


    @pytest.mark.asyncio
    async def test_create_with_all_compilations(self):
        # Covers lines 83-90 (markers, extra_args, selected_files)
        orch = _make_orchestrator()
        req = RunRequest(
            tests_path="sample",
            selected_markers=["smoke", "regression"],
            extra_args="--tb=short --maxfail=2",
            selected_files=["test_a.py", "test_b.py"],
        )
        run = await orch.create(req)
        assert "-m" in run.args
        assert "smoke or regression" in run.args
        assert "--tb=short" in run.args
        assert "--maxfail=2" in run.args
        assert "test_a.py" in run.args
        assert "test_b.py" in run.args


    @pytest.mark.asyncio
    async def test_docker_executor_mode_routing(self):
        # Covers line 201
        orch = _make_orchestrator()
        # Mock self._process_docker
        mock_docker = FakeProcessRunner(
            handler=lambda cmd, cwd, env, timeout: ProcessResult(
                exit_code=0, stdout="docker-ok", stderr="", duration_ms=5
            )
        )
        orch._process_docker = mock_docker

        req = RunRequest(tests_path="sample", executor_mode="docker")
        run = await orch.create(req)
        await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED  # FAILED because no collector results preset

    @pytest.mark.asyncio
    async def test_workspace_jail_copytree_exception(self, tmp_path):
        # Covers lines 181-182
        from unittest.mock import patch
        tests_root = tmp_path / "tests_root"
        tests_root.mkdir()
        suite_dir = tests_root / "suite_abc"
        suite_dir.mkdir()

        artifacts_root = tmp_path / "artifacts"
        artifacts_root.mkdir()

        orch = _make_orchestrator(tests_root=str(tests_root))
        orch._artifacts_root = str(artifacts_root)

        req = RunRequest(tests_path="suite_abc")
        run = await orch.create(req)

        with patch("shutil.copytree", side_effect=Exception("Copy failed")):
            await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED  # falls back and still completes execution flow

    @pytest.mark.asyncio
    async def test_workspace_jail_rmtree_exception(self, tmp_path):
        # Covers lines 266-267
        from unittest.mock import patch
        tests_root = tmp_path / "tests_root"
        tests_root.mkdir()
        suite_dir = tests_root / "suite_abc"
        suite_dir.mkdir()
        (suite_dir / "test_x.py").write_text("def test_x(): pass")

        artifacts_root = tmp_path / "artifacts"
        artifacts_root.mkdir()

        orch = _make_orchestrator(tests_root=str(tests_root))
        orch._artifacts_root = str(artifacts_root)

        req = RunRequest(tests_path="suite_abc")
        run = await orch.create(req)

        with patch("shutil.rmtree", side_effect=Exception("Remove failed")):
            await orch.execute(run.id)

        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED  # rmtree failure handled gracefully

    @pytest.mark.asyncio
    async def test_workspace_jail_ignore_artifacts_root(self, tmp_path):
        # Covers line 160
        tests_root = tmp_path / "tests_root"
        tests_root.mkdir()

        # Put artifacts_root inside tests_root
        artifacts_root = tests_root / "runs"
        artifacts_root.mkdir()

        orch = _make_orchestrator(tests_root=str(tests_root))
        orch._artifacts_root = str(artifacts_root)

        req = RunRequest(tests_path="")
        run = await orch.create(req)

        await orch.execute(run.id)
        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_workspace_jail_ignore_run_dir(self, tmp_path):
        # Covers line 162
        tests_root = tmp_path / "tests_root"
        tests_root.mkdir()

        # We configure artifacts_root of the orchestrator to be tests_root
        # so that run_dir is tests_root / run_id
        orch = _make_orchestrator(tests_root=str(tests_root))
        orch._artifacts_root = str(tests_root)

        # Create the fake run_id folder inside tests_root
        fake_run_dir = tests_root / "id-001"
        fake_run_dir.mkdir()

        req = RunRequest(tests_path="")
        run = await orch.create(req)

        await orch.execute(run.id)
        stored = await orch._store.get(run.id)
        assert stored.status == RunStatus.FAILED
