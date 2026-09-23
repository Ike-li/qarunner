"""Tests for DockerRunner adapter using mocks."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docker.errors import APIError, ImageNotFound, NotFound

from qarunner.adapters.docker_runner import (
    DockerRunner,
    build_source_tarball,
    extract_results_archive,
    rewrite_results_paths,
)
from qarunner.errors import RunnerError, UnsafePath


def _source_dir(tmp_path: Path) -> str:
    """A minimal real source directory — build_source_tarball reads cwd
    through real filesystem I/O now, unlike the old bind-mount design where
    cwd was never actually touched locally."""
    suite = tmp_path / "suite"
    suite.mkdir(exist_ok=True)
    (suite / "test_sample.py").write_text("def test_sample(): pass\n")
    return str(suite)


class MockImage:
    def __init__(self, tag: str) -> None:
        self.tags = [tag]


class FakeVolume:
    def __init__(self, name: str = "vol-1", *, remove_fails: bool = False) -> None:
        self.name = name
        self.removed = False
        self.remove_fails = remove_fails

    def remove(self, force: bool = False) -> None:
        self.removed = True
        if self.remove_fails:
            raise Exception("Volume remove failed")


class FakeAPIClient:
    """Stands in for ``DockerClient.api`` — the low-level surface DockerRunner
    uses to run the real command (the container's own PID 1 is just a `sleep`
    placeholder; docker-py's high-level ``exec_run``'s streamed form doesn't
    expose the exec_id needed to retrieve an exit code once the stream is
    drained, so DockerRunner talks to this low-level surface directly)."""

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: bytes = b"hello",
        stderr: bytes = b"error",
        exec_start_raises: Exception | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.exec_start_raises = exec_start_raises
        self.exec_create_calls: list[dict] = []
        self.exec_inspect_calls: list[str] = []

    def exec_create(self, container_id, cmd, environment=None, workdir=None, **kwargs):
        self.exec_create_calls.append(
            {
                "container_id": container_id,
                "cmd": cmd,
                "environment": environment,
                "workdir": workdir,
            }
        )
        return {"Id": "exec-1"}

    def exec_start(self, exec_id, stream=True, demux=True):
        if self.exec_start_raises:
            raise self.exec_start_raises
        if self.stdout:
            yield (self.stdout, None)
        if self.stderr:
            yield (None, self.stderr)

    def exec_inspect(self, exec_id):
        self.exec_inspect_calls.append(exec_id)
        return {"ExitCode": self.exit_code}


class MockContainer:
    def __init__(
        self,
        *,
        get_archive_result=None,
        kill_fails: bool = False,
        remove_fails: bool = False,
        is_falsy: bool = False,
    ) -> None:
        self.id = "container-1"
        self.started = False
        self.put_archive_calls: list[tuple] = []
        self.get_archive_calls: list[str] = []
        self.get_archive_result = get_archive_result
        self.kill_called = False
        self.remove_called = False
        self.kill_fails = kill_fails
        self.remove_fails = remove_fails
        self.is_falsy = is_falsy

    def __bool__(self) -> bool:
        return not self.is_falsy

    def start(self) -> None:
        self.started = True

    def put_archive(self, path, data) -> None:
        self.put_archive_calls.append((path, data))

    def get_archive(self, path):
        self.get_archive_calls.append(path)
        if isinstance(self.get_archive_result, Exception):
            raise self.get_archive_result
        if self.get_archive_result is not None:
            return self.get_archive_result
        # Real Docker always returns a validly-formed tar, even for an empty
        # directory (at minimum the wrapper directory entry itself) — never
        # truly empty bytes, so the default fake matches that shape.
        wrapper = path.rsplit("/", 1)[-1]
        return (iter([_get_archive_style_tar(wrapper, {})]), {})

    def kill(self) -> None:
        self.kill_called = True
        if self.kill_fails:
            raise Exception("Kill failed")

    def remove(self, force: bool = False) -> None:
        self.remove_called = True
        if self.remove_fails:
            raise Exception("Remove failed")


class MockClient:
    def __init__(
        self,
        *,
        images_exist: bool = True,
        exit_code: int = 0,
        exec_start_raises: Exception | None = None,
        kill_fails: bool = False,
        remove_fails: bool = False,
        is_container_falsy: bool = False,
        get_archive_result=None,
        volume_remove_fails: bool = False,
        volume_create_raises: Exception | None = None,
        create_raises: Exception | None = None,
        put_archive_raises: Exception | None = None,
    ) -> None:
        self.images_exist = images_exist
        self.build_called = False
        self.create_called = False
        self.create_args = None
        self.create_kwargs = None
        self.mock_container: MockContainer | None = None
        self.mock_volume: FakeVolume | None = None
        self._create_raises = create_raises
        self._put_archive_raises = put_archive_raises
        self._kill_fails = kill_fails
        self._remove_fails = remove_fails
        self._is_container_falsy = is_container_falsy
        self._get_archive_result = get_archive_result
        self._volume_remove_fails = volume_remove_fails
        self._volume_create_raises = volume_create_raises

        self.images = MagicMock()
        self.images.get.side_effect = self._get_image
        self.images.build.side_effect = self._build_image

        self.containers = MagicMock()
        self.containers.create.side_effect = self._create_container

        self.volumes = MagicMock()
        self.volumes.create.side_effect = self._create_volume

        self.api = FakeAPIClient(exit_code=exit_code, exec_start_raises=exec_start_raises)

    def _get_image(self, tag: str) -> MockImage:
        if not self.images_exist:
            self.images_exist = True
            raise ImageNotFound("Image not found")
        return MockImage(tag)

    def _build_image(self, *args, **kwargs) -> tuple[MockImage, list]:
        self.build_called = True
        return MockImage("qarunner-executor:latest"), []

    def _create_volume(self, *args, **kwargs) -> FakeVolume:
        if self._volume_create_raises:
            raise self._volume_create_raises
        self.mock_volume = FakeVolume(remove_fails=self._volume_remove_fails)
        return self.mock_volume

    def _create_container(self, *args, **kwargs) -> MockContainer:
        if self._create_raises:
            raise self._create_raises
        self.create_called = True
        self.create_args = args
        self.create_kwargs = kwargs
        container = MockContainer(
            get_archive_result=self._get_archive_result,
            kill_fails=self._kill_fails,
            remove_fails=self._remove_fails,
            is_falsy=self._is_container_falsy,
        )
        if self._put_archive_raises is not None:
            exc = self._put_archive_raises

            def _raising_put_archive(path, data, _exc=exc):
                raise _exc

            container.put_archive = _raising_put_archive
        self.mock_container = container
        return container


async def test_docker_runner_success(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True, exit_code=0)
    runner = DockerRunner(client=mock_client)

    cmd = ["/usr/bin/python", "-m", "pytest", "--junitxml=/tmp/res/junit.xml"]
    result = await runner.run(cmd, cwd=_source_dir(tmp_path), timeout=10)

    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "error" in result.stderr
    assert result.timed_out is False
    assert mock_client.create_called is True

    # The container's own command is a placeholder; the real (python-mapped,
    # results-path-rewritten) command runs via exec instead.
    assert mock_client.create_kwargs["command"][0] == "sleep"
    assert mock_client.create_kwargs["working_dir"] == "/workspace"
    assert mock_client.create_kwargs["volumes"] == {
        mock_client.mock_volume.name: {"bind": "/workspace", "mode": "rw"},
    }
    # SEC-3: container runs with least privilege.
    assert mock_client.create_kwargs["network_mode"] == "none"
    assert mock_client.create_kwargs["cap_drop"] == ["ALL"]
    assert mock_client.create_kwargs["security_opt"] == ["no-new-privileges"]
    assert mock_client.create_kwargs["pids_limit"] == 512
    assert mock_client.create_kwargs["read_only"] is True
    assert "user" in mock_client.create_kwargs

    exec_call = mock_client.api.exec_create_calls[0]
    assert exec_call["cmd"] == [
        "python",
        "-m",
        "pytest",
        "--junitxml=/workspace/.qarunner-results/junit.xml",
    ]
    assert exec_call["workdir"] == "/workspace"

    assert mock_client.mock_container.started is True
    put_path, put_data = mock_client.mock_container.put_archive_calls[0]
    assert put_path == "/workspace"
    with tarfile.open(fileobj=io.BytesIO(put_data)) as tar:
        assert "test_sample.py" in tar.getnames()
    assert mock_client.mock_container.remove_called is True
    assert mock_client.mock_volume.removed is True


async def test_docker_runner_bounds_accumulated_output(tmp_path: Path) -> None:
    # OOM guard: the exec-stream drain bounds how much it accumulates for the
    # in-memory ProcessResult (a test emitting unbounded output can't OOM the
    # platform), even though the full stream still lands on disk if a
    # stdout_file/stderr_file was requested.
    from qarunner.adapters.docker_runner import _MAX_LOG_BYTES

    mock_client = MockClient(images_exist=True)
    mock_client.api.stdout = b"x" * (_MAX_LOG_BYTES + 1000)
    mock_client.api.stderr = b"y" * (_MAX_LOG_BYTES + 1000)
    runner = DockerRunner(client=mock_client)

    result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path))

    assert len(result.stdout.encode()) <= _MAX_LOG_BYTES
    assert len(result.stderr.encode()) <= _MAX_LOG_BYTES


async def test_docker_runner_falsy_container(tmp_path: Path) -> None:
    # Falsy container covers the "if container:" false branch at the end.
    mock_client = MockClient(images_exist=True, is_container_falsy=True)
    runner = DockerRunner(client=mock_client)

    result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path))

    assert result.exit_code == 0
    assert mock_client.mock_container.remove_called is False  # falsy container skips remove
    assert mock_client.mock_volume.removed is True  # volume cleanup is unconditional


async def test_docker_runner_alternate_args_and_mapping(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)
    source = _source_dir(tmp_path)

    # 1. empty command
    await runner.run([], cwd=source)
    assert mock_client.api.exec_create_calls[-1]["cmd"] == []

    # 2. python3 mapping & --alluredir extraction, redirected to the fixed
    # in-container results path (no host-path-identical mount needed).
    await runner.run(
        ["python3", "-m", "pytest", "--alluredir=/tmp/allure/allure-results"], cwd=source
    )
    assert mock_client.api.exec_create_calls[-1]["cmd"] == [
        "python",
        "-m",
        "pytest",
        "--alluredir=/workspace/.qarunner-results/allure-results",
    ]

    # 3. non-python, non-path command without a results dir: no rewriting,
    # and no get_archive call since nothing is expected back.
    await runner.run(["pytest", "-k", "test_math"], cwd=source)
    assert mock_client.api.exec_create_calls[-1]["cmd"] == ["pytest", "-k", "test_math"]
    assert mock_client.mock_container.get_archive_calls == []


async def test_docker_runner_uses_playwright_image_and_env_junit_mount(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd=_source_dir(tmp_path),
        env={"PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml"},
    )

    assert mock_client.create_args == ("qarunner-playwright-executor:latest",)
    exec_call = mock_client.api.exec_create_calls[-1]
    assert exec_call["cmd"] == ["playwright", "test", "--reporter=junit"]
    assert exec_call["environment"]["HOME"] == "/tmp"
    assert exec_call["environment"]["XDG_CACHE_HOME"] == "/tmp/.cache"
    assert exec_call["environment"]["PLAYWRIGHT_BROWSERS_PATH"] == "/ms-playwright"
    assert (
        exec_call["environment"]["NODE_PATH"]
        == "/usr/local/lib/node_modules:/usr/lib/node_modules"
    )
    assert (
        exec_call["environment"]["PLAYWRIGHT_JUNIT_OUTPUT_NAME"]
        == "/workspace/.qarunner-results/junit.xml"
    )
    assert mock_client.create_kwargs["shm_size"] == "1g"
    # T-M5-BROWSER-001 red lines: never privileged / SYS_ADMIN / host IPC.
    assert mock_client.create_kwargs.get("privileged") is False
    assert "cap_add" not in mock_client.create_kwargs
    assert mock_client.create_kwargs.get("ipc_mode") != "host"
    assert mock_client.create_kwargs["cap_drop"] == ["ALL"]
    assert mock_client.create_kwargs["network_mode"] == "none"
    assert mock_client.create_kwargs["security_opt"] == ["no-new-privileges"]
    # Non-root even if the host process is root (dev backend container).
    user = mock_client.create_kwargs["user"]
    assert user not in {"0", "0:0", "root", "root:root"}
    assert not str(user).startswith("0:")


async def test_docker_runner_precreates_results_dir_owned_by_sandbox_user(
    tmp_path: Path,
) -> None:
    # Regression: with a root backend the Playwright sandbox still runs as 1000:1000,
    # and the fresh /workspace volume is root-owned — Playwright died with EACCES on
    # mkdir /workspace/.qarunner-results before running a single test.
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    with (
        patch("qarunner.adapters.docker_runner.os.getuid", return_value=0),
        patch("qarunner.adapters.docker_runner.os.getgid", return_value=0),
    ):
        await runner.run(
            ["npx", "playwright", "test", "--reporter=junit"],
            cwd=_source_dir(tmp_path),
            env={"PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml"},
        )

    assert mock_client.create_kwargs["user"] == "1000:1000"
    _path, put_data = mock_client.mock_container.put_archive_calls[0]
    with tarfile.open(fileobj=io.BytesIO(put_data)) as tar:
        results = tar.getmember(".qarunner-results")
    assert results.isdir()
    assert (results.uid, results.gid) == (1000, 1000)


@pytest.mark.parametrize(("host_uid", "host_gid"), [(0, 0), (501, 20)])
async def test_docker_runner_sandbox_runs_as_fixed_image_user(
    tmp_path: Path, host_uid: int, host_gid: int
) -> None:
    # The sandbox always runs as the executor images' baked non-root user, which owns
    # the images' /workspace — never as the backend's own uid (root in dev, anything
    # elsewhere). Nothing crosses back via file ownership: source goes in through
    # put_archive and results come out through get_archive.
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    with (
        patch("qarunner.adapters.docker_runner.os.getuid", return_value=host_uid),
        patch("qarunner.adapters.docker_runner.os.getgid", return_value=host_gid),
    ):
        await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path))

    assert mock_client.create_kwargs["user"] == "1000:1000"
    _path, put_data = mock_client.mock_container.put_archive_calls[0]
    with tarfile.open(fileobj=io.BytesIO(put_data)) as tar:
        assert {(m.uid, m.gid) for m in tar.getmembers()} == {(1000, 1000)}


async def test_docker_runner_mounts_allowlisted_env_directory_readonly(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    repo = project_root / "my-app"
    repo.mkdir(parents=True)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client, extra_readonly_roots=[str(project_root)])

    await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd=_source_dir(tmp_path),
        env={
            "PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml",
            "APP_REPO_PATH": str(repo),
            "UNRELATED_PATH": str(unrelated),
        },
    )

    assert mock_client.create_kwargs["volumes"][str(repo)] == {"bind": str(repo), "mode": "ro"}
    assert str(unrelated) not in mock_client.create_kwargs["volumes"]


async def test_docker_runner_skips_relative_and_file_env_mounts(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    config_file = project_root / "config.json"
    config_file.write_text("{}")

    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client, extra_readonly_roots=[str(project_root)])

    await runner.run(
        ["npx", "playwright", "test", "--reporter=junit"],
        cwd=_source_dir(tmp_path),
        env={
            "RELATIVE_REPO": "relative/path",
            "CONFIG_FILE": str(config_file),
            "PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/tmp/results/junit.xml",
        },
    )

    assert str(config_file) not in mock_client.create_kwargs["volumes"]


async def test_docker_runner_playwright_image_not_found_builds_playwright_dockerfile(
    tmp_path: Path,
) -> None:
    mock_client = MockClient(images_exist=False)
    runner = DockerRunner(client=mock_client)

    await runner.run(["npx", "playwright", "test", "--reporter=junit"], cwd=_source_dir(tmp_path))

    assert mock_client.build_called is True
    assert mock_client.images.build.call_args.kwargs["dockerfile"] == "Dockerfile.playwright"
    assert (
        mock_client.images.build.call_args.kwargs["tag"] == "qarunner-playwright-executor:latest"
    )


async def test_docker_runner_image_not_found_causes_build(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=False)
    runner = DockerRunner(client=mock_client)

    cmd = ["python", "-m", "pytest"]
    result = await runner.run(cmd, cwd=_source_dir(tmp_path), timeout=10)

    assert result.exit_code == 0
    assert mock_client.build_called is True
    assert mock_client.create_called is True


async def test_docker_runner_image_not_found_build_disabled_raises(tmp_path: Path) -> None:
    # DEP-5: with runtime build disabled, a missing executor image fails fast
    # instead of being silently (re)built.
    mock_client = MockClient(images_exist=False)
    runner = DockerRunner(client=mock_client, allow_runtime_build=False)

    with pytest.raises(RunnerError, match="runtime build is disabled"):
        await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path), timeout=10)

    assert mock_client.build_called is False
    assert mock_client.create_called is False


async def test_docker_runner_timeout(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    with patch("asyncio.wait_for", AsyncMock(side_effect=TimeoutError)):
        result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path), timeout=1)

    assert result.exit_code == 137
    assert result.timed_out is True
    assert mock_client.mock_container.kill_called is True
    assert mock_client.mock_container.remove_called is True
    assert mock_client.mock_volume.removed is True


async def test_docker_runner_timeout_kill_fails(tmp_path: Path) -> None:
    # If container.kill raises during timeout handling, we log it and proceed.
    mock_client = MockClient(images_exist=True, kill_fails=True)
    runner = DockerRunner(client=mock_client)

    with patch("asyncio.wait_for", AsyncMock(side_effect=TimeoutError)):
        result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path), timeout=1)

    assert result.exit_code == 137
    assert result.timed_out is True


async def test_docker_runner_infra_failure_kill_fails(tmp_path: Path) -> None:
    # Mirrors test_docker_runner_timeout_kill_fails for the non-timeout branch.
    mock_client = MockClient(
        images_exist=True, exec_start_raises=APIError("daemon connection reset"), kill_fails=True
    )
    runner = DockerRunner(client=mock_client)

    result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path), timeout=1)

    assert result.exit_code == 137
    assert result.timed_out is False
    assert mock_client.mock_container.kill_called is True


async def test_docker_runner_infra_failure_is_not_reported_as_timeout(tmp_path: Path) -> None:
    """BUG-10, carried into the exec model: a Docker daemon/API failure while
    running the real command (connection reset, daemon restart, etc.) is not
    the same thing as the run's own timeout elapsing — the two must stay
    distinguishable so infra failures aren't misreported as "the test suite
    ran too long". The container is still force-killed either way (its state
    is unknown)."""
    mock_client = MockClient(
        images_exist=True, exec_start_raises=APIError("daemon connection reset")
    )
    runner = DockerRunner(client=mock_client)

    result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path), timeout=1)

    assert result.exit_code == 137
    assert result.timed_out is False
    assert mock_client.mock_container.kill_called is True
    assert mock_client.mock_container.remove_called is True


async def test_docker_runner_remove_fails(tmp_path: Path) -> None:
    # If container.remove raises in the finally block, we handle it gracefully.
    mock_client = MockClient(images_exist=True, remove_fails=True)
    runner = DockerRunner(client=mock_client)

    result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path))

    assert result.exit_code == 0
    assert mock_client.mock_container.remove_called is True
    assert mock_client.mock_volume.removed is True  # not skipped by the container's failure


async def test_docker_runner_volume_remove_fails(tmp_path: Path) -> None:
    # Mirrors test_docker_runner_remove_fails for the volume cleanup step —
    # container removal must not be skipped by the volume's failure either
    # (each cleanup step is independently best-effort).
    mock_client = MockClient(images_exist=True, volume_remove_fails=True)
    runner = DockerRunner(client=mock_client)

    result = await runner.run(["python", "-m", "pytest"], cwd=_source_dir(tmp_path))

    assert result.exit_code == 0
    assert mock_client.mock_container.remove_called is True
    assert mock_client.mock_volume.removed is True


async def test_docker_runner_init_failure(tmp_path: Path) -> None:
    # docker.from_env() raising during client lazy loading.
    with patch("docker.from_env", side_effect=Exception("Docker socket missing")):
        runner = DockerRunner()
        with pytest.raises(RunnerError, match="Docker initialization failed"):
            await runner.run(["python"], cwd=_source_dir(tmp_path))


async def test_docker_runner_container_create_failure_cleans_up_volume(tmp_path: Path) -> None:
    # The volume is created before the container; if container creation then
    # fails, the volume must still be cleaned up rather than leaked.
    mock_client = MockClient(images_exist=True, create_raises=Exception("Docker daemon exploded"))
    runner = DockerRunner(client=mock_client)

    with pytest.raises(RunnerError, match="Docker container execution failed"):
        await runner.run(["python"], cwd=_source_dir(tmp_path))

    assert mock_client.mock_volume is not None
    assert mock_client.mock_volume.removed is True


async def test_docker_runner_put_archive_failure_cleans_up(tmp_path: Path) -> None:
    # A new failure window that didn't exist under the old bind-mount design:
    # put_archive can fail between create() and start(). Both the container
    # and the volume must still be cleaned up.
    mock_client = MockClient(images_exist=True, put_archive_raises=Exception("archive rejected"))
    runner = DockerRunner(client=mock_client)

    with pytest.raises(RunnerError, match="Docker container execution failed"):
        await runner.run(["python"], cwd=_source_dir(tmp_path))

    assert mock_client.mock_container.remove_called is True
    assert mock_client.mock_volume.removed is True


async def test_docker_runner_source_directory_missing(tmp_path: Path) -> None:
    # Path.rglob() on a missing directory silently yields nothing rather than
    # raising, which would otherwise inject an empty tarball and run the
    # sandboxed command against nothing — this must fail loudly instead,
    # before any container/volume is created.
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    with pytest.raises(RunnerError, match="does not exist"):
        await runner.run(["python"], cwd=str(tmp_path / "does-not-exist"))

    assert mock_client.mock_container is None
    assert mock_client.mock_volume is None


async def test_docker_runner_source_packaging_failure(tmp_path: Path) -> None:
    # The directory exists (passes the earlier existence check) but reading
    # it fails partway through (e.g. a permission error, or a file removed in
    # a race between listing and reading) — build_source_tarball raises
    # OSError, which run() wraps rather than propagating raw.
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    with (
        patch(
            "qarunner.adapters.docker_runner.build_source_tarball",
            side_effect=OSError("disk read error"),
        ),
        pytest.raises(RunnerError, match="Failed to package source directory"),
    ):
        await runner.run(["python"], cwd=_source_dir(tmp_path))

    assert mock_client.mock_container is None
    assert mock_client.mock_volume is None


async def test_docker_runner_volume_create_failure(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True, volume_create_raises=Exception("daemon exploded"))
    runner = DockerRunner(client=mock_client)

    with pytest.raises(RunnerError, match="Docker volume creation failed"):
        await runner.run(["python"], cwd=_source_dir(tmp_path))

    assert mock_client.create_called is False  # never got as far as the container


async def test_docker_runner_timeout_then_results_gather_failure_still_reports_timeout(
    tmp_path: Path,
) -> None:
    """Once a real timeout is already known, a *different* problem while
    gathering results (not a missing-results NotFound) must not override that
    story with a raised RunnerError — the timeout is the more informative
    fact about what happened."""
    mock_client = MockClient(
        images_exist=True, get_archive_result=Exception("results gather exploded")
    )
    runner = DockerRunner(client=mock_client)

    with patch("asyncio.wait_for", AsyncMock(side_effect=TimeoutError)):
        result = await runner.run(
            ["python", "-m", "pytest", "--junitxml=/tmp/res/junit.xml"],
            cwd=_source_dir(tmp_path),
            timeout=1,
        )

    assert result.exit_code == 137
    assert result.timed_out is True


def test_docker_runner_lazy_loading() -> None:
    # _get_client correctly lazy-loads from env if none was passed.
    runner = DockerRunner()
    with patch("docker.from_env") as mock_from_env:
        client = runner._get_client()
        mock_from_env.assert_called_once()
        assert client == mock_from_env.return_value


def test_docker_runner_find_project_root_with_temp_dir(tmp_path: Path) -> None:
    # _find_project_root covers all branches, run in a fake tmp_path workspace.
    adapter_dir = tmp_path / "src" / "qarunner" / "adapters"
    adapter_dir.mkdir(parents=True, exist_ok=True)

    runner = DockerRunner()

    # 1. Finding Dockerfile
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.touch()

    fake_file_path = adapter_dir / "docker_runner.py"
    with patch("pathlib.Path.resolve", return_value=fake_file_path):
        root = runner._find_project_root()
        assert root == tmp_path

    # 2. Finding pyproject.toml instead of Dockerfile
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

    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    result = await runner.run(
        ["python", "-m", "pytest"],
        cwd=_source_dir(tmp_path),
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
    )

    assert result.exit_code == 0
    assert stdout_file.read_text() == "hello"
    assert stderr_file.read_text() == "error"


async def test_docker_runner_forwards_labels(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)
    await runner.run(
        ["python", "-m", "pytest"],
        cwd=_source_dir(tmp_path),
        timeout=10,
        labels={"qarunner.attempt_id": "attempt-001", "qarunner.fence": "1"},
    )
    assert mock_client.create_kwargs["labels"]["qarunner.attempt_id"] == "attempt-001"
    assert mock_client.create_kwargs["labels"]["qarunner.fence"] == "1"


async def test_docker_runner_skips_get_archive_when_no_results_expected(tmp_path: Path) -> None:
    mock_client = MockClient(images_exist=True)
    runner = DockerRunner(client=mock_client)

    await runner.run(["pytest", "-k", "test_math"], cwd=_source_dir(tmp_path))

    assert mock_client.mock_container.get_archive_calls == []


async def test_docker_runner_get_archive_not_found_is_silently_ignored(tmp_path: Path) -> None:
    # The command never produced results (e.g. crashed before writing) — same
    # as the old bind-mount behavior where a missing file was simply absent.
    mock_client = MockClient(images_exist=True, get_archive_result=NotFound("no such path"))
    runner = DockerRunner(client=mock_client)

    result = await runner.run(
        ["python", "-m", "pytest", "--junitxml=/tmp/res/junit.xml"], cwd=_source_dir(tmp_path)
    )

    assert result.exit_code == 0


async def test_docker_runner_injects_source_and_extracts_results(tmp_path: Path) -> None:
    """End-to-end (mocked) proof that run() wires build_source_tarball,
    put_archive, get_archive, and extract_results_archive together
    correctly, without any host-path relationship between cwd/results_dir
    and whatever daemon actually creates the sandbox container."""
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_a.py").write_text("def test_a(): pass\n")
    results_dir = tmp_path / "results"

    archive = _get_archive_style_tar(".qarunner-results", {"junit.xml": b"<xml/>"})
    mock_client = MockClient(images_exist=True, get_archive_result=(iter([archive]), {}))
    runner = DockerRunner(client=mock_client)

    await runner.run(
        ["python", "-m", "pytest", f"--junitxml={results_dir}/junit.xml"],
        cwd=str(source),
        timeout=10,
    )

    put_path, put_data = mock_client.mock_container.put_archive_calls[0]
    assert put_path == "/workspace"
    with tarfile.open(fileobj=io.BytesIO(put_data)) as tar:
        assert "test_a.py" in tar.getnames()

    assert (results_dir / "junit.xml").read_bytes() == b"<xml/>"
    assert mock_client.mock_container.get_archive_calls == ["/workspace/.qarunner-results"]


# ── docker-cp helpers: no host-path-identity required (pure filesystem/tar) ──
#
# These replace the bind-mount-based cwd/results_dir transfer: build_source_tarball
# packages a directory to inject via put_archive, extract_results_archive unpacks
# what get_archive returns, and rewrite_results_paths redirects any argv/env value
# that pointed at the caller's host-facing results dir to the fixed in-container
# one. None of this needs a real or mocked Docker client.


def test_build_source_tarball_uses_root_relative_arcnames(tmp_path: Path) -> None:
    source = tmp_path / "suite"
    (source / "sub").mkdir(parents=True)
    (source / "test_a.py").write_text("def test_a(): pass\n")
    (source / "sub" / "test_b.py").write_text("def test_b(): pass\n")

    data = build_source_tarball(str(source), uid=1234, gid=5678)

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
        assert names == {"test_a.py", "sub/test_b.py"}
        for member in tar.getmembers():
            assert member.uid == 1234
            assert member.gid == 5678


def test_build_source_tarball_excludes_jail_ignore_names(tmp_path: Path) -> None:
    source = tmp_path / "suite"
    (source / ".git").mkdir(parents=True)
    (source / ".git" / "config").write_text("dummy")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "pkg.js").write_text("// dep")
    (source / "test_a.py").write_text("def test_a(): pass\n")

    data = build_source_tarball(str(source), uid=0, gid=0)

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = {m.name for m in tar.getmembers()}
        assert not any(n.startswith(".git") for n in names)
        assert not any(n.startswith("__pycache__") for n in names)
        # node_modules must survive (Playwright red line, §5.7).
        assert "node_modules/pkg.js" in names
        assert "test_a.py" in names


def test_build_source_tarball_includes_directories_owned_by_sandbox_user(
    tmp_path: Path,
) -> None:
    # put_archive only chowns what the tar names: a directory without its own entry
    # is created by the daemon as root:root 0755, which a non-root sandbox cannot
    # write into.
    source = tmp_path / "suite"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "test_b.py").write_text("def test_b(): pass\n")

    data = build_source_tarball(str(source), uid=1234, gid=5678)

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = tar.getmembers()
    names = [m.name for m in members]
    sub = members[names.index("sub")]
    assert sub.isdir()
    assert (sub.uid, sub.gid) == (1234, 5678)
    # The entry must precede its contents, or the daemon creates the dir first.
    assert names.index("sub") < names.index("sub/test_b.py")


def test_build_source_tarball_skips_symlinked_directories(tmp_path: Path) -> None:
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_a.py").write_text("def test_a(): pass\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not part of the suite")
    (source / "linked").symlink_to(outside, target_is_directory=True)

    data = build_source_tarball(str(source), uid=0, gid=0)

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = tar.getnames()
    assert not any(n == "linked" or n.startswith("linked/") for n in names)
    assert "test_a.py" in names


def test_build_source_tarball_precreates_writable_dirs(tmp_path: Path) -> None:
    # The workdir itself is the volume mount point and stays root-owned (the daemon
    # ignores a "." entry), so a non-root sandbox cannot mkdir anything directly
    # under it — dirs it must write to have to arrive in the tarball.
    source = tmp_path / "suite"
    source.mkdir()
    (source / "test_a.py").write_text("def test_a(): pass\n")

    data = build_source_tarball(
        str(source), uid=1000, gid=1000, writable_dirs=[".qarunner-results"]
    )

    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        results = tar.getmember(".qarunner-results")
    assert results.isdir()
    assert (results.uid, results.gid) == (1000, 1000)
    assert results.mode & 0o700 == 0o700


def _get_archive_style_tar(wrapper: str, files: dict[str, bytes]) -> bytes:
    """Build a tar shaped like docker's get_archive(dir) response: every entry
    (including the requested dir itself) is prefixed by its own basename."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dir_info = tarfile.TarInfo(name=wrapper)
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)
        for relname, data in files.items():
            info = tarfile.TarInfo(name=f"{wrapper}/{relname}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


def test_extract_results_archive_strips_wrapper_prefix(tmp_path: Path) -> None:
    data = _get_archive_style_tar(
        ".qarunner-results",
        {"junit.xml": b"<xml/>", "allure-results/result.json": b"{}"},
    )
    dest = tmp_path / "results"

    extract_results_archive(data, str(dest))

    assert (dest / "junit.xml").read_bytes() == b"<xml/>"
    assert (dest / "allure-results" / "result.json").read_bytes() == b"{}"


def test_extract_results_archive_rejects_path_traversal(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dir_info = tarfile.TarInfo(name="w")
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)
        evil = tarfile.TarInfo(name="w/../../evil.txt")
        data = b"pwned"
        evil.size = len(data)
        tar.addfile(evil, io.BytesIO(data))
    buf.seek(0)
    dest = tmp_path / "results"

    with pytest.raises(UnsafePath):
        extract_results_archive(buf.read(), str(dest))


def test_extract_results_archive_handles_truly_empty_tar(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w"):
        pass  # zero members, not even a wrapper dir entry
    buf.seek(0)
    dest = tmp_path / "results"

    extract_results_archive(buf.read(), str(dest))  # must not raise

    assert not dest.exists()


def test_extract_results_archive_skips_wrapper_entry_named_without_trailing_content(
    tmp_path: Path,
) -> None:
    """A non-directory-typed member whose name exactly equals the wrapper
    prefix (edge case, e.g. a malformed archive) resolves to an empty
    relative path and must be skipped rather than crash on safe_subpath."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        odd = tarfile.TarInfo(name="w/")
        odd.size = 0
        tar.addfile(odd, io.BytesIO(b""))
    buf.seek(0)
    dest = tmp_path / "results"

    extract_results_archive(buf.read(), str(dest))  # must not raise

    assert list(dest.rglob("*")) == [] if dest.exists() else True


def test_extract_results_archive_skips_members_extractfile_cannot_open(tmp_path: Path) -> None:
    """A member type tarfile.extractfile() returns None for (e.g. a device or
    FIFO special file) is skipped rather than crashing on a None read()."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dir_info = tarfile.TarInfo(name="w")
        dir_info.type = tarfile.DIRTYPE
        tar.addfile(dir_info)
        fifo = tarfile.TarInfo(name="w/pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
    buf.seek(0)
    dest = tmp_path / "results"

    extract_results_archive(buf.read(), str(dest))  # must not raise

    assert not (dest / "pipe").exists()


def test_rewrite_results_paths_redirects_recognized_flags_and_env() -> None:
    argv = [
        "python",
        "-m",
        "pytest",
        "--junitxml=/host/results/junit.xml",
        "--alluredir=/host/results/allure-results",
        "-o",
        "addopts=",
    ]
    env = {"PLAYWRIGHT_JUNIT_OUTPUT_NAME": "/host/results/junit.xml", "FOO": "bar"}

    new_argv, new_env = rewrite_results_paths(
        argv,
        env,
        host_results_dir="/host/results",
        container_results_dir="/workspace/.qarunner-results",
    )

    assert "--junitxml=/workspace/.qarunner-results/junit.xml" in new_argv
    assert "--alluredir=/workspace/.qarunner-results/allure-results" in new_argv
    assert new_argv[:3] == ["python", "-m", "pytest"]  # untouched tokens preserved
    assert new_env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] == "/workspace/.qarunner-results/junit.xml"
    assert new_env["FOO"] == "bar"  # untouched


def test_rewrite_results_paths_generalizes_to_any_matching_flag() -> None:
    """Not a hand-maintained flag allowlist: --output= (Playwright's artifact
    dir) is also under host_results_dir and must be redirected the same way,
    without needing its own special case."""
    argv = ["npx", "playwright", "test", "--output=/host/results/playwright-results"]

    new_argv, _ = rewrite_results_paths(
        argv,
        {},
        host_results_dir="/host/results",
        container_results_dir="/workspace/.qarunner-results",
    )

    assert "--output=/workspace/.qarunner-results/playwright-results" in new_argv


def test_rewrite_results_paths_does_not_over_match_sibling_directory() -> None:
    """A directory that merely shares a string prefix with host_results_dir
    (but isn't actually nested under it) must not be rewritten."""
    argv = ["--alluredir=/host/results-old/allure-results"]

    new_argv, _ = rewrite_results_paths(
        argv,
        {},
        host_results_dir="/host/results",
        container_results_dir="/workspace/.qarunner-results",
    )

    assert new_argv == argv
