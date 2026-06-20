"""Isolated process runner using Docker containers."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import ImageNotFound

from qarunner.errors import RunnerError
from qarunner.models import ProcessResult

if TYPE_CHECKING:
    from docker import DockerClient

logger = logging.getLogger(__name__)


class DockerRunner:
    """Execute pytest commands inside an isolated Docker container."""

    def __init__(self, client: DockerClient | None = None) -> None:
        self._client = client

    def _get_client(self) -> DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _ensure_image(self, client: DockerClient) -> None:
        try:
            client.images.get("qarunner-executor:latest")
        except ImageNotFound:
            logger.info("Base image 'qarunner-executor:latest' not found. Building...")
            root = self._find_project_root()
            client.images.build(
                path=str(root),
                tag="qarunner-executor:latest",
                rm=True,
            )
            logger.info("Base image 'qarunner-executor:latest' built successfully.")

    def _find_project_root(self) -> Path:
        curr = Path(__file__).resolve()
        for parent in curr.parents:
            if (parent / "Dockerfile").exists():
                return parent
            if (parent / "pyproject.toml").exists():
                return parent
        return Path.cwd()

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
        stdout_file: str | None = None,
        stderr_file: str | None = None,
    ) -> ProcessResult:
        start_time = time.monotonic()
        timed_out = False
        exit_code = -1
        stdout_bytes = b""
        stderr_bytes = b""

        # 1. Get client & ensure image exists
        try:
            client = await asyncio.to_thread(self._get_client)
            await asyncio.to_thread(self._ensure_image, client)
        except Exception as exc:
            raise RunnerError(f"Docker initialization failed: {exc}") from exc

        # 2. Command rewriting: swap virtualenv python with python inside container
        mapped_cmd = list(cmd)
        if mapped_cmd and (
            mapped_cmd[0].endswith("python")
            or mapped_cmd[0].endswith("python3")
            or "/" in mapped_cmd[0]
            or "\\" in mapped_cmd[0]
        ):
            mapped_cmd[0] = "python"

        # 3. Mount definitions: Extract results_dir to mount
        results_dir = None
        for arg in cmd:
            if arg.startswith("--junitxml="):
                results_path = arg.split("=", 1)[1]
                results_dir = os.path.dirname(results_path)
            elif arg.startswith("--alluredir="):
                results_path = arg.split("=", 1)[1]
                results_dir = os.path.dirname(results_path)

        # Pre-create results_dir on host to avoid permission/root creation issues
        if results_dir:
            os.makedirs(results_dir, exist_ok=True)

        volumes = {}
        # Mount tests_dir (cwd)
        volumes[cwd] = {"bind": cwd, "mode": "rw"}
        # Mount results_dir if different
        if results_dir and results_dir != cwd:
            volumes[results_dir] = {"bind": results_dir, "mode": "rw"}

        container = None
        log_task = None
        try:
            # 4. Run container in detached mode
            def _start_container():
                return client.containers.run(
                    "qarunner-executor:latest",
                    command=mapped_cmd,
                    volumes=volumes,
                    working_dir=cwd,
                    environment=env or {},
                    detach=True,
                    stdout=True,
                    stderr=True,
                )

            container = await asyncio.to_thread(_start_container)

            # Polling-based log streamer to write logs in real-time
            async def log_streamer():
                while True:
                    try:
                        container.reload()
                    except Exception:
                        break
                    try:
                        out_bytes = container.logs(stdout=True, stderr=False)
                        err_bytes = container.logs(stdout=False, stderr=True)
                        if stdout_file:
                            os.makedirs(os.path.dirname(stdout_file), exist_ok=True)
                            with open(stdout_file, "wb") as f:
                                f.write(out_bytes)
                        if stderr_file:
                            os.makedirs(os.path.dirname(stderr_file), exist_ok=True)
                            with open(stderr_file, "wb") as f:
                                f.write(err_bytes)
                    except Exception:
                        pass
                    if getattr(container, "status", "") != "running":
                        break
                    await asyncio.sleep(1.0)

            log_task = asyncio.create_task(log_streamer())

            # 5. Wait for completion with timeout
            def _wait_container():
                return container.wait(timeout=timeout)

            try:
                wait_result = await asyncio.to_thread(_wait_container)
                exit_code = wait_result.get("StatusCode", -1)
            except Exception:
                timed_out = True
                logger.warning("Container execution timed out or failed. Killing container...")
                try:
                    await asyncio.to_thread(container.kill)
                except Exception as kill_exc:
                    logger.warning("Failed to kill container: %s", kill_exc)
                exit_code = 137  # Standard SIGKILL exit code
            finally:
                if log_task:
                    log_task.cancel()
                    try:
                        await log_task
                    except asyncio.CancelledError:
                        pass

            # 6. Gather logs
            def _get_logs():
                return (
                    container.logs(stdout=True, stderr=False),
                    container.logs(stdout=False, stderr=True),
                )

            stdout_bytes, stderr_bytes = await asyncio.to_thread(_get_logs)
            if stdout_file:
                os.makedirs(os.path.dirname(stdout_file), exist_ok=True)
                with open(stdout_file, "wb") as f:
                    f.write(stdout_bytes)
            if stderr_file:
                os.makedirs(os.path.dirname(stderr_file), exist_ok=True)
                with open(stderr_file, "wb") as f:
                    f.write(stderr_bytes)

        except Exception as exc:
            if not timed_out:
                raise RunnerError(f"Docker container execution failed: {exc}") from exc
        finally:
            if container:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception:
                    pass

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return ProcessResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            duration_ms=elapsed_ms,
            timed_out=timed_out,
        )

