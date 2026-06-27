"""Tests for DockerRunner adapter using mocks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import DockerException, ImageNotFound

from qarunner.adapters.docker_runner import DockerRunner
from qarunner.errors import RunnerError


class MockImage:

    def __init__(self, tag: str) -> None:
        self.tags = [tag]


class MockContainer:

    def __init__(
        self,
        wait_status: int | str = 0,
        logs_stdout: bytes = b"hello",
        logs_stderr: bytes = b"error",
        kill_fails: bool = False,
        remove_fails: bool = False,
        is_falsy: bool = False,
    ) -> None:
        self.wait_status = wait_status
        self.logs_stdout = logs_stdout
        self.logs_stderr = logs_stderr
        self.kill_called = False
        self.remove_called = False
        self.kill_fails = kill_fails
        self.remove_fails = remove_fails
        self.is_falsy = is_falsy
        self.status = "running"
        self.logs_tails: list[int | None] = []

    def reload(self) -> None:
        self.status = "completed"

    def __bool__(self) -> bool:
        return not self.is_falsy

    def wait(self, timeout: int | None = None) -> dict[str, int]:
        if self.wait_status == "timeout":
            raise Exception("Timeout")
        return {"StatusCode": int(self.wait_status)}

    def kill(self) -> None:
        self.kill_called = True
        if self.kill_fails:
            raise Exception("Kill failed")

    def remove(self, force: bool = False) -> None:
        self.remove_called = True
        if self.remove_fails:
            raise Exception("Remove failed")

    def logs(self, stdout: bool = True, stderr: bool = True, tail: int | None = None) -> bytes:
        self.logs_tails.append(tail)
        if self.wait_status == "timeout":
            # Fail log retrieval during timeout to hit the outer except (timed_out=True)
            raise Exception("Logs failed during timeout")
        if stdout:
            return self.logs_stdout
        return self.logs_stderr



class MockClient:

    def __init__(
        self,
        images_exist: bool = True,
        wait_status: int | str = 0,
        kill_fails: bool = False,
        remove_fails: bool = False,
        is_container_falsy: bool = False,
    ) -> None:
        self.images_exist = images_exist
        self.wait_status = wait_status
        self.kill_fails = kill_fails
        self.remove_fails = remove_fails
        self.is_container_falsy = is_container_falsy
        self.build_called = False
        self.run_called = False
        self.run_args = None
        self.run_kwargs = None
        self.mock_container = None

        # Images API
        self.images = MagicMock()
        self.images.get.side_effect = self._get_image
        self.images.build.side_effect = self._build_image

        # Containers API
        self.containers = MagicMock()
        self.containers.run.side_effect = self._run_container

    def _get_image(self, tag: str) -> MockImage:
        if not self.images_exist:
            self.images_exist = True
            raise ImageNotFound("Image not found")
        return MockImage(tag)

    def _build_image(self, *args, **kwargs) -> tuple[MockImage, list]:
        self.build_called = True
        return MockImage("qarunner-executor:latest"), []

    def _run_container(self, *args, **kwargs) -> MockContainer:
        self.run_called = True
        self.run_args = args
        self.run_kwargs = kwargs
        self.mock_container = MockContainer(
            wait_status=self.wait_status,
            kill_fails=self.kill_fails,
            remove_fails=self.remove_fails,
            is_falsy=self.is_container_falsy,
        )
        return self.mock_container


async def test_docker_runner_success() -> None:
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["/usr/bin/python", "-m", "pytest", "--junitxml=/tmp/res/junit.xml"]
    result = await runner.run(cmd, cwd="/tmp/tests", timeout=10)

    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "error" in result.stderr
    assert result.timed_out is False
    assert mock_client.run_called is True
    assert mock_client.run_kwargs["command"] == [
        "python", "-m", "pytest", "--junitxml=/tmp/res/junit.xml",
    ]
    assert mock_client.run_kwargs["volumes"] == {
        "/tmp/tests": {"bind": "/tmp/tests", "mode": "rw"},
        "/tmp/res": {"bind": "/tmp/res", "mode": "rw"},
    }
    assert mock_client.mock_container.remove_called is True
    # SEC-3: container runs with least privilege.
    assert mock_client.run_kwargs["network_mode"] == "none"
    assert mock_client.run_kwargs["cap_drop"] == ["ALL"]
    assert mock_client.run_kwargs["security_opt"] == ["no-new-privileges"]
    assert mock_client.run_kwargs["pids_limit"] == 512
    assert "user" in mock_client.run_kwargs


async def test_docker_runner_bounds_final_log_read() -> None:
    # OOM guard: the final log gather caps how much it pulls from the finished
    # container (docker tails by line, like subprocess's byte cap) so a test
    # emitting GBs of output can't be read fully into memory.
    from qarunner.adapters.docker_runner import _MAX_LOG_LINES

    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)
    await runner.run(["python", "-m", "pytest"], cwd="/tmp/tests", timeout=10)
    assert _MAX_LOG_LINES in mock_client.mock_container.logs_tails


async def test_docker_runner_falsy_container() -> None:
    # Falsy container covers the "if container:" false branch at the end
    mock_client = MockClient(images_exist=True, wait_status=0, is_container_falsy=True)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]
    result = await runner.run(cmd, cwd="/tmp/tests")

    assert result.exit_code == 0
    assert mock_client.mock_container.remove_called is False  # falsy container skips remove


async def test_docker_runner_alternate_args_and_mapping() -> None:
    # Test all variations of command rewriting and --alluredir extraction
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    # 1. Test empty command
    await runner.run([], cwd="/tmp/tests")
    assert mock_client.run_kwargs["command"] == []

    # 2. Test python3 mapping & --alluredir extraction with subfolder to match dirname
    await runner.run(
        ["python3", "-m", "pytest", "--alluredir=/tmp/allure/allure-results"], cwd="/tmp/tests"
    )
    assert mock_client.run_kwargs["command"] == [
        "python", "-m", "pytest", "--alluredir=/tmp/allure/allure-results",
    ]
    assert mock_client.run_kwargs["volumes"] == {
        "/tmp/tests": {"bind": "/tmp/tests", "mode": "rw"},
        "/tmp/allure": {"bind": "/tmp/allure", "mode": "rw"},
    }

    # 3. Test non-python, non-path command without results_dir
    await runner.run(["pytest", "-k", "test_math"], cwd="/tmp/tests")
    assert mock_client.run_kwargs["command"] == ["pytest", "-k", "test_math"]
    assert mock_client.run_kwargs["volumes"] == {
        "/tmp/tests": {"bind": "/tmp/tests", "mode": "rw"},
    }


async def test_docker_runner_uses_playwright_image_and_env_junit_mount() -> None:
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd="/tmp/playwright-suite",
        env={"PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml"},
    )

    assert mock_client.run_args == ("qarunner-playwright-executor:latest",)
    assert mock_client.run_kwargs["command"] == ["playwright", "test", "--reporter=junit"]
    assert mock_client.run_kwargs["volumes"] == {
        "/tmp/playwright-suite": {"bind": "/tmp/playwright-suite", "mode": "rw"},
        "/tmp/results": {"bind": "/tmp/results", "mode": "rw"},
    }
    assert mock_client.run_kwargs["environment"]["HOME"] == "/tmp"
    assert mock_client.run_kwargs["environment"]["XDG_CACHE_HOME"] == "/tmp/.cache"
    assert mock_client.run_kwargs["environment"]["PLAYWRIGHT_BROWSERS_PATH"] == "/ms-playwright"
    assert mock_client.run_kwargs["shm_size"] == "1g"


async def test_docker_runner_mounts_allowlisted_env_directory_readonly(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    repo = project_root / "my-app"
    repo.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client, extra_readonly_roots=[str(project_root)])

    await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd="/tmp/playwright-suite",
        env={
            "PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml",
            "APP_REPO_PATH": str(repo),
            "UNRELATED_PATH": str(unrelated),
        },
    )

    assert mock_client.run_kwargs["volumes"][str(repo)] == {"bind": str(repo), "mode": "ro"}
    assert str(unrelated) not in mock_client.run_kwargs["volumes"]


async def test_docker_runner_skips_relative_and_file_env_mounts(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    config_file = project_root / "config.json"
    config_file.write_text("{}")

    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client, extra_readonly_roots=[str(project_root)])

    await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd="/tmp/playwright-suite",
        env={
            "RELATIVE_REPO": "relative/path",
            "CONFIG_FILE": str(config_file),
            "PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml",
        },
    )

    assert str(config_file) not in mock_client.run_kwargs["volumes"]


async def test_docker_runner_playwright_image_not_found_builds_playwright_dockerfile() -> None:
    mock_client = MockClient(images_exist=False, wait_status=0)
    runner = DockerRunner(client=mock_client)

    await runner.run(["npx", "playwright", "test", "--reporter=junit"], cwd="/tmp/tests")

    assert mock_client.build_called is True
    assert mock_client.images.build.call_args.kwargs["dockerfile"] == "Dockerfile.playwright"
    assert (
        mock_client.images.build.call_args.kwargs["tag"]
        == "qarunner-playwright-executor:latest"
    )


async def test_docker_runner_image_not_found_causes_build() -> None:
    mock_client = MockClient(images_exist=False, wait_status=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]
    result = await runner.run(cmd, cwd="/tmp/tests", timeout=10)

    assert result.exit_code == 0
    assert mock_client.build_called is True
    assert mock_client.run_called is True


async def test_docker_runner_image_not_found_build_disabled_raises() -> None:
    # DEP-5: with runtime build disabled, a missing executor image fails fast
    # instead of being silently (re)built.
    mock_client = MockClient(images_exist=False, wait_status=0)
    runner = DockerRunner(client=mock_client, allow_runtime_build=False)

    with pytest.raises(RunnerError, match="runtime build is disabled"):
        await runner.run(["python", "-m", "pytest"], cwd="/tmp/tests", timeout=10)

    assert mock_client.build_called is False
    assert mock_client.run_called is False


async def test_docker_runner_timeout() -> None:
    mock_client = MockClient(images_exist=True, wait_status="timeout")
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]
    result = await runner.run(cmd, cwd="/tmp/tests", timeout=1)

    assert result.exit_code == 137
    assert result.timed_out is True
    assert mock_client.mock_container.kill_called is True
    assert mock_client.mock_container.remove_called is True


async def test_docker_runner_timeout_kill_fails() -> None:
    # Test that if container.kill raises exception during timeout, we log it and proceed
    mock_client = MockClient(images_exist=True, wait_status="timeout", kill_fails=True)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]
    result = await runner.run(cmd, cwd="/tmp/tests", timeout=1)

    assert result.exit_code == 137
    assert result.timed_out is True
    assert mock_client.mock_container.kill_called is True
    assert mock_client.mock_container.remove_called is True


async def test_docker_runner_remove_fails() -> None:
    # Test that if container.remove raises exception in finally, we handle it gracefully
    mock_client = MockClient(images_exist=True, wait_status=0, remove_fails=True)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]
    result = await runner.run(cmd, cwd="/tmp/tests")

    assert result.exit_code == 0
    assert mock_client.mock_container.remove_called is True


async def test_docker_runner_init_failure() -> None:
    # Test when docker.from_env() raises an exception during client lazy loading
    with patch("docker.from_env", side_effect=Exception("Docker socket missing")):
        runner = DockerRunner()
        with pytest.raises(RunnerError, match="Docker initialization failed"):
            await runner.run(["python"], cwd="/tmp/tests")


async def test_docker_runner_generic_run_failure() -> None:
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    # Force client.containers.run to raise exception
    mock_client.containers.run.side_effect = Exception("Docker daemon exploded")

    with pytest.raises(RunnerError, match="Docker container execution failed"):
        await runner.run(["python"], cwd="/tmp/tests")


def test_docker_runner_lazy_loading() -> None:
    # Test that _get_client correctly lazy-loads from env if none was passed
    runner = DockerRunner()
    with patch("docker.from_env") as mock_from_env:
        client = runner._get_client()
        mock_from_env.assert_called_once()
        assert client == mock_from_env.return_value


def test_docker_runner_find_project_root_with_temp_dir(tmp_path: Path) -> None:
    # Test that _find_project_root covers all branches by running in a fake tmp_path workspace
    adapter_dir = tmp_path / "src" / "qarunner" / "adapters"
    adapter_dir.mkdir(parents=True, exist_ok=True)

    runner = DockerRunner()

    # 1. Test finding Dockerfile
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.touch()

    fake_file_path = adapter_dir / "docker_runner.py"
    with patch("pathlib.Path.resolve", return_value=fake_file_path):
        root = runner._find_project_root()
        assert root == tmp_path

    # 2. Test finding pyproject.toml instead of Dockerfile
    dockerfile.unlink()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.touch()

    with patch("pathlib.Path.resolve", return_value=fake_file_path):
        root = runner._find_project_root()
        assert root == tmp_path

    # 3. Fallback to Path.cwd() when neither exists
    pyproject.unlink()
    with patch("pathlib.Path.resolve", return_value=fake_file_path):
        root = runner._find_project_root()
        assert root == Path.cwd()


async def test_docker_runner_stdout_stderr_files(tmp_path: Path) -> None:
    stdout_file = tmp_path / "logs" / "stdout.log"
    stderr_file = tmp_path / "logs" / "stderr.log"

    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]

    with patch("asyncio.sleep", AsyncMock()):
        result = await runner.run(
            cmd,
            cwd="/tmp/tests",
            stdout_file=str(stdout_file),
            stderr_file=str(stderr_file),
        )

    assert result.exit_code == 0
    assert stdout_file.exists()
    assert stderr_file.exists()
    assert stdout_file.read_text() == "hello"
    assert stderr_file.read_text() == "error"


async def test_docker_runner_streamer_reload_exception() -> None:
    # Test when reload() raises an exception during streaming (streamer breaks)
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]

    original_run_container = mock_client._run_container
    def reload_failing_container(*args, **kwargs) -> MockContainer:
        container = original_run_container(*args, **kwargs)
        container.reload = MagicMock(side_effect=DockerException("Reload failed"))
        return container

    mock_client.containers.run.side_effect = reload_failing_container

    with patch("asyncio.sleep", AsyncMock()):
        result = await runner.run(cmd, cwd="/tmp/tests")

    assert result.exit_code == 0
    assert result.stdout == "hello"


async def test_docker_runner_streamer_logs_exception() -> None:
    # Test when logs() raises an exception inside the streamer loop but succeeds later
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]

    original_run_container = mock_client._run_container
    def logs_failing_container(*args, **kwargs) -> MockContainer:
        container = original_run_container(*args, **kwargs)

        # Fail the first streamer logs() call so the narrowed DockerException
        # handler runs once; later calls (final gather) succeed. reload() flips
        # status to "completed", so the loop exits after this single iteration.
        container_logs_called = 0
        def logs_side_effect(stdout=True, stderr=True, tail=None):
            nonlocal container_logs_called
            container_logs_called += 1
            if container_logs_called <= 1:
                raise DockerException("Logs failed during stream")
            return b"hello" if stdout else b"error"

        container.logs = MagicMock(side_effect=logs_side_effect)
        return container

    mock_client.containers.run.side_effect = logs_failing_container

    with patch("asyncio.sleep", AsyncMock()):
        result = await runner.run(cmd, cwd="/tmp/tests")

    assert result.exit_code == 0
    assert result.stdout == "hello"


async def test_docker_runner_streamer_cancellation_and_sleep() -> None:
    # Test to hit await asyncio.sleep(1.0) and task cancellation error handling
    import asyncio
    import time
    mock_client = MockClient(images_exist=True, wait_status=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]

    original_run_container = mock_client._run_container
    def sleeping_container(*args, **kwargs) -> MockContainer:
        container = original_run_container(*args, **kwargs)

        # Override reload to be a no-op, status remains "running"
        container.reload = MagicMock()
        container.status = "running"

        # Override wait to sleep in the background thread, allowing streamer to run
        def mock_wait(timeout=None):
            time.sleep(0.05)
            return {"StatusCode": 0}
        container.wait = MagicMock(side_effect=mock_wait)
        return container

    mock_client.containers.run.side_effect = sleeping_container

    original_sleep = asyncio.sleep
    # Mock asyncio.sleep to yield control immediately (0.001 seconds sleep)
    async def mock_async_sleep(delay):
        await original_sleep(0.001)

    with patch("asyncio.sleep", side_effect=mock_async_sleep):
        result = await runner.run(cmd, cwd="/tmp/tests")

    assert result.exit_code == 0
    assert result.stdout == "hello"
