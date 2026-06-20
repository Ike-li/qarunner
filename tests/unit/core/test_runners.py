"""Tests for qarunner.core.runners — PytestRunner + RunnerRegistry."""

import pytest

from qarunner.core.runners.base import BuildContext
from qarunner.core.runners.pytest_runner import PytestRunner
from qarunner.core.runners.registry import RunnerRegistry
from qarunner.errors import UnknownRunner


class TestPytestRunner:
    """PytestRunner.build_command produces correct CLI args."""

    def test_name(self):
        runner = PytestRunner()
        assert runner.name == "pytest"

    def test_basic_command(self):
        runner = PytestRunner()
        ctx = BuildContext(
            tests_dir="/work/tests",
            results_dir="/artifacts/run-001/results",
            executable="/usr/bin/python3",
            args=[],
        )
        cmd = runner.build_command(ctx)
        assert cmd == [
            "/usr/bin/python3",
            "-m",
            "pytest",
            "--junitxml=/artifacts/run-001/results/junit.xml",
            "--alluredir=/artifacts/run-001/results/allure-results",
        ]

    def test_extra_args_appended(self):
        runner = PytestRunner()
        ctx = BuildContext(
            tests_dir="/work/tests",
            results_dir="/artifacts/run-001/results",
            executable="python",
            args=["-k", "test_login", "--timeout=30"],
        )
        cmd = runner.build_command(ctx)
        assert cmd[-3:] == ["-k", "test_login", "--timeout=30"]
        assert cmd[0] == "python"

    def test_build_context_is_frozen(self):
        ctx = BuildContext(
            tests_dir="/a",
            results_dir="/b",
            executable="python",
            args=[],
        )
        with pytest.raises(AttributeError):
            ctx.tests_dir = "/changed"  # type: ignore[misc]


class TestRunnerRegistry:
    """RunnerRegistry maps names to Runner instances."""

    def test_register_and_get(self):
        registry = RunnerRegistry()
        runner = PytestRunner()
        registry.register(runner)
        assert registry.get("pytest") is runner

    def test_get_unknown_raises(self):
        registry = RunnerRegistry()
        with pytest.raises(UnknownRunner):
            registry.get("nope")

    def test_register_multiple(self):
        registry = RunnerRegistry()
        r1 = PytestRunner()
        registry.register(r1)

        # Register a second (same name, replaces)
        r2 = PytestRunner()
        registry.register(r2)
        assert registry.get("pytest") is r2
