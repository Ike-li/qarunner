"""Isolated process runner using Docker containers."""

from __future__ import annotations

import asyncio
import contextlib
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

# Cap how many trailing log lines the final gather pulls from a finished
# container into memory, so a test emitting unbounded output can't OOM the
# platform (subprocess caps bytes; docker's logs API can only tail by line).
_MAX_LOG_LINES = 50_000


class DockerRunner:
    """Execute pytest commands inside an isolated Docker container."""

    def __init__(
        self, client: DockerClient | None = None, *, allow_runtime_build: bool = True
    ) -> None:
        self._client = client
        self._allow_runtime_build = allow_runtime_build

    def _get_client(self) -> DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _ensure_image(self, client: DockerClient) -> None:
        try:
            client.images.get("qarunner-executor:latest")
        except ImageNotFound:
            if not self._allow_runtime_build:
                # DEP-5: in production the executor image must be pre-built. A
                # silent runtime build re-resolves the Dockerfile and can drift
                # (and masks a missing image), so fail fast instead.
                raise RunnerError(
                    "Executor image 'qarunner-executor:latest' not found and runtime "
                    "build is disabled (QARUNNER_EXECUTOR_AUTOBUILD=false). Pre-build it: "
                    "docker build -f Dockerfile -t qarunner-executor:latest ."
                ) from None
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
            if arg.startswith("--junitxml=") or arg.startswith("--alluredir="):
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
                    # SEC-3: execute untrusted test code with least privilege.
                    # Run as the host caller (non-root unless the platform itself
                    # is root) so files written to bind mounts stay owned by us.
                    user=f"{os.getuid()}:{os.getgid()}",
                    network_mode="none",
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges"],
                    pids_limit=512,
                    mem_limit="2g",
                    nano_cpus=2_000_000_000,  # SEC-3: cap CPU at 2.0 cores.
                    # SEC-3: read-only root filesystem so untrusted test code
                    # can't tamper with the image. The bind-mounted cwd and
                    # results dir stay writable (declared above); pytest's own
                    # temp/cache needs a writable /tmp, supplied as a tmpfs.
                    read_only=True,
                    tmpfs={"/tmp": ""},
                )

            container = await asyncio.to_thread(_start_container)

            # Streaming-based log streamer to write logs in real-time
            async def log_streamer():
                def _stream(is_stdout: bool):
                    try:
                        filepath = stdout_file if is_stdout else stderr_file
                        container.reload()
                        try:
                            stream = container.logs(
                                stream=True,
                                follow=True,
                                stdout=is_stdout,
                                stderr=not is_stdout,
                            )
                        except TypeError:
                            stream = container.logs(
                                stdout=is_stdout,
                                stderr=not is_stdout,
                            )
                        chunks = [stream] if isinstance(stream, bytes) else stream
                        if filepath:
                            os.makedirs(os.path.dirname(filepath), exist_ok=True)
                            with open(filepath, "ab") as f:
                                for chunk in chunks:
                                    f.write(chunk)
                                    f.flush()
                        else:
                            for _ in chunks:
                                pass
                    except Exception:
                        logger.warning("Failed to stream container logs to file", exc_info=True)

                stdout_fut = asyncio.to_thread(_stream, True)
                stderr_fut = asyncio.to_thread(_stream, False)
                await asyncio.gather(stdout_fut, stderr_fut, return_exceptions=True)

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
                log_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await log_task


            # 6. Gather logs
            def _get_logs():
                return (
                    container.logs(stdout=True, stderr=False, tail=_MAX_LOG_LINES),
                    container.logs(stdout=False, stderr=True, tail=_MAX_LOG_LINES),
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
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(container.remove, force=True)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return ProcessResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            duration_ms=elapsed_ms,
            timed_out=timed_out,
        )

