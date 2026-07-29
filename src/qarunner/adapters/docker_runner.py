"""Isolated process runner using Docker containers."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import tarfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.errors import ImageNotFound, NotFound

from qarunner.core.paths import JAIL_IGNORE_NAMES, safe_subpath
from qarunner.errors import RunnerError
from qarunner.models import ProcessResult

if TYPE_CHECKING:
    from docker import DockerClient

logger = logging.getLogger(__name__)

# Cap how many trailing bytes the exec-stream drain keeps in memory per
# stream, so a test emitting unbounded output can't OOM the platform (the
# full stream still lands on stdout_file/stderr_file on disk; only the bytes
# kept for the in-memory ProcessResult are bounded). Byte-bounded equivalent
# of the old container.logs(tail=50_000 lines) cap, now that output comes
# from a live exec stream rather than a one-shot post-hoc log read.
_MAX_LOG_BYTES = 10_000_000

# Fixed in-container paths the sandbox always uses, regardless of what host
# path the caller's own process sees `cwd`/results at — see build_source_tarball
# / extract_results_archive / rewrite_results_paths module docstrings.
_IN_CONTAINER_WORKDIR = "/workspace"
_IN_CONTAINER_RESULTS_DIR = f"{_IN_CONTAINER_WORKDIR}/.qarunner-results"


def build_source_tarball(source_dir: str, *, uid: int, gid: int) -> bytes:
    """Package *source_dir* as an in-memory tar for ``put_archive`` injection.

    Arcnames are root-relative (no leading path component) so extracting into
    a fixed in-container directory reproduces the source tree there exactly,
    regardless of what host path the calling process's own filesystem sees
    *source_dir* at — replacing the bind-mount's host-path-identity
    requirement with a plain byte payload. Entry ownership is forced to
    *uid*/*gid* (the same values passed as the sandbox's non-root exec
    ``user=``) rather than preserved from whatever uid happens to own the
    files on the calling process's filesystem, so the sandboxed process can
    always read/write what it's given regardless of who created it.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        root = Path(source_dir)
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in JAIL_IGNORE_NAMES for part in relative.parts):
                continue
            if path.is_dir():
                continue
            info = tar.gettarinfo(str(path), arcname=str(relative))
            info.uid = uid
            info.gid = gid
            with open(path, "rb") as f:
                tar.addfile(info, f)
    return buf.getvalue()


def extract_results_archive(tar_bytes: bytes, dest_dir: str) -> None:
    """Unpack a ``get_archive`` response into *dest_dir*.

    ``get_archive(path)`` wraps every entry in a leading component named for
    *path*'s own basename (confirmed empirically against the Engine API, not
    documented); this strips that wrapper so *dest_dir* mirrors the requested
    directory's contents directly. Each member resolves through
    ``safe_subpath`` so a crafted member name (``../../evil``) can't escape
    *dest_dir* — the archive comes from inside the sandbox that just executed
    untrusted test code.
    """
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        members = tar.getmembers()
        if not members:
            return
        wrapper_prefix = f"{members[0].name.split('/', 1)[0]}/"
        for member in members:
            if member.isdir() or not member.name.startswith(wrapper_prefix):
                continue
            relative = member.name[len(wrapper_prefix) :]
            if not relative:
                continue
            dest_path = safe_subpath(dest_dir, relative)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(extracted.read())


def rewrite_results_paths(
    argv: list[str],
    env: dict[str, str],
    *,
    host_results_dir: str,
    container_results_dir: str,
) -> tuple[list[str], dict[str, str]]:
    """Redirect any ``--flag=value``/env value under *host_results_dir* to
    *container_results_dir*.

    Generalized over every matching value rather than a hand-maintained list
    of recognized flags, so a results-shaped flag we don't special-case by
    name never silently falls through as a broken host-only path now that
    there's no bind mount making the two paths the same thing.
    """
    host_root = Path(host_results_dir)

    def _rewrite(value: str) -> str:
        try:
            relative = Path(value).relative_to(host_root)
        except ValueError:
            return value
        return str(Path(container_results_dir) / relative)

    new_argv = []
    for token in argv:
        if token.startswith("--") and "=" in token:
            flag, _, value = token.partition("=")
            new_argv.append(f"{flag}={_rewrite(value)}")
        else:
            new_argv.append(token)

    new_env = {key: _rewrite(value) for key, value in env.items()}
    return new_argv, new_env


def _drain_exec_stream(
    api_client, exec_id: str, stdout_file: str | None, stderr_file: str | None
) -> tuple[bytes, bytes]:
    """Blocking: consume a demuxed ``exec_start(stream=True)`` generator.

    Writes each chunk to *stdout_file*/*stderr_file* as it arrives, so a live
    log-follow (e.g. the platform's SSE endpoint tailing stdout.log) sees
    real-time output — the same live-tail behavior the old container.logs()
    streaming task gave, now sourced from the exec stream instead. The
    in-memory accumulation kept for the returned bytes is bounded to
    ``_MAX_LOG_BYTES`` (a sliding window, trimmed as chunks arrive — not just
    at the end) while the on-disk files still receive everything.
    """
    stdout_acc = bytearray()
    stderr_acc = bytearray()
    with contextlib.ExitStack() as files:
        stdout_fh = None
        stderr_fh = None
        if stdout_file:
            os.makedirs(os.path.dirname(stdout_file), exist_ok=True)
            stdout_fh = files.enter_context(open(stdout_file, "ab"))
        if stderr_file:
            os.makedirs(os.path.dirname(stderr_file), exist_ok=True)
            stderr_fh = files.enter_context(open(stderr_file, "ab"))

        for stdout_chunk, stderr_chunk in api_client.exec_start(exec_id, stream=True, demux=True):
            if stdout_chunk:
                stdout_acc.extend(stdout_chunk)
                if len(stdout_acc) > _MAX_LOG_BYTES:
                    del stdout_acc[: len(stdout_acc) - _MAX_LOG_BYTES]
                if stdout_fh:
                    stdout_fh.write(stdout_chunk)
                    stdout_fh.flush()
            if stderr_chunk:
                stderr_acc.extend(stderr_chunk)
                if len(stderr_acc) > _MAX_LOG_BYTES:
                    del stderr_acc[: len(stderr_acc) - _MAX_LOG_BYTES]
                if stderr_fh:
                    stderr_fh.write(stderr_chunk)
                    stderr_fh.flush()
    return bytes(stdout_acc), bytes(stderr_acc)


class DockerRunner:
    """Execute pytest commands inside an isolated Docker container."""

    def __init__(
        self,
        client: DockerClient | None = None,
        *,
        allow_runtime_build: bool = True,
        executor_image: str = "qarunner-executor:latest",
        playwright_executor_image: str = "qarunner-playwright-executor:latest",
        extra_readonly_roots: Sequence[str] | None = None,
    ) -> None:
        self._client = client
        self._allow_runtime_build = allow_runtime_build
        self._executor_image = executor_image
        self._playwright_executor_image = playwright_executor_image
        self._extra_readonly_roots = [
            Path(root).expanduser().resolve() for root in (extra_readonly_roots or []) if root
        ]

    def _get_client(self) -> DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _ensure_image(
        self, client: DockerClient, image: str, *, dockerfile: str | None = None
    ) -> None:
        try:
            client.images.get(image)
        except ImageNotFound:
            if not self._allow_runtime_build:
                # DEP-5: in production the executor image must be pre-built. A
                # silent runtime build re-resolves the Dockerfile and can drift
                # (and masks a missing image), so fail fast instead.
                raise RunnerError(
                    f"Executor image {image!r} not found and runtime build is disabled "
                    "(QARUNNER_EXECUTOR_AUTOBUILD=false). Pre-build it with the "
                    "matching Dockerfile."
                ) from None
            logger.info("Executor image %r not found. Building...", image)
            root = self._find_project_root()
            build_kwargs = {"path": str(root), "tag": image, "rm": True}
            if dockerfile:
                build_kwargs["dockerfile"] = dockerfile
            client.images.build(**build_kwargs)
            logger.info("Executor image %r built successfully.", image)

    def _find_project_root(self) -> Path:
        curr = Path(__file__).resolve()
        for parent in curr.parents:
            if (parent / "Dockerfile").exists():
                return parent
            if (parent / "pyproject.toml").exists():
                return parent
        return Path.cwd()

    def _image_for_command(self, cmd: list[str]) -> tuple[str, str | None, bool]:
        is_playwright = len(cmd) >= 3 and cmd[:3] == ["npx", "playwright", "test"]
        if is_playwright:
            return self._playwright_executor_image, "Dockerfile.playwright", True
        return self._executor_image, None, False

    def _extra_readonly_volumes(self, env: dict[str, str]) -> dict[str, dict[str, str]]:
        volumes: dict[str, dict[str, str]] = {}
        if not self._extra_readonly_roots:
            return volumes

        for value in env.values():
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_dir():
                continue
            for root in self._extra_readonly_roots:
                if resolved == root or resolved.is_relative_to(root):
                    host_path = str(resolved)
                    volumes[host_path] = {"bind": host_path, "mode": "ro"}
                    break
        return volumes

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
        stdout_file: str | None = None,
        stderr_file: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> ProcessResult:
        start_time = time.monotonic()
        timed_out = False
        exit_code = -1
        stdout_bytes = b""
        stderr_bytes = b""
        proc_env = dict(env or {})
        image, dockerfile, is_playwright = self._image_for_command(cmd)
        if is_playwright:
            proc_env["HOME"] = "/tmp"
            proc_env["XDG_CACHE_HOME"] = "/tmp/.cache"
            proc_env["PLAYWRIGHT_BROWSERS_PATH"] = "/ms-playwright"
            proc_env["NODE_PATH"] = "/usr/local/lib/node_modules:/usr/lib/node_modules"

        # 1. Get client & ensure image exists
        try:
            client = await asyncio.to_thread(self._get_client)
            await asyncio.to_thread(self._ensure_image, client, image, dockerfile=dockerfile)
        except Exception as exc:
            raise RunnerError(f"Docker initialization failed: {exc}") from exc

        # 2. Command rewriting: swap virtualenv python with python inside container
        mapped_cmd = list(cmd)
        if is_playwright and mapped_cmd[:2] == ["npx", "playwright"]:
            mapped_cmd = ["playwright", *mapped_cmd[2:]]
        if mapped_cmd and (
            mapped_cmd[0].endswith("python")
            or mapped_cmd[0].endswith("python3")
            or "/" in mapped_cmd[0]
            or "\\" in mapped_cmd[0]
        ):
            mapped_cmd[0] = "python"

        # 3. Detect the caller's host-facing results dir (same detection as
        # before), then redirect every matching argv/env value to the fixed
        # in-container results path — this replaces the bind mount that used
        # to make the host and container paths the same thing. extra_readonly
        # volumes are resolved from the *un-rewritten* env (unrelated paths).
        host_results_dir = None
        for arg in cmd:
            if (
                arg.startswith("--junitxml=")
                or arg.startswith("--alluredir=")
                or arg.startswith("--output=")
            ):
                results_path = arg.split("=", 1)[1]
                host_results_dir = os.path.dirname(results_path)
        if proc_env.get("PLAYWRIGHT_JUNIT_OUTPUT_NAME"):
            host_results_dir = os.path.dirname(proc_env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"])

        if host_results_dir:
            os.makedirs(host_results_dir, exist_ok=True)
            exec_cmd, exec_env = rewrite_results_paths(
                mapped_cmd,
                proc_env,
                host_results_dir=host_results_dir,
                container_results_dir=_IN_CONTAINER_RESULTS_DIR,
            )
        else:
            exec_cmd, exec_env = mapped_cmd, dict(proc_env)

        extra_volumes = self._extra_readonly_volumes(proc_env)

        # 4. Package the source directory as an in-memory tar (put_archive
        # payload) — reads only through the calling process's own filesystem
        # view of *cwd*, so it needs no host-path relationship to whatever
        # daemon ends up creating the sandbox container.
        if not os.path.isdir(cwd):
            # rglob() on a missing path silently yields nothing rather than
            # raising, which would otherwise inject an empty tarball and run
            # the sandboxed command against nothing — fail loudly instead.
            raise RunnerError(f"source directory {cwd!r} does not exist")
        uid, gid = os.getuid(), os.getgid()
        # T-M5-BROWSER-001: Chromium must not run as root even when the
        # control-plane process is root (backend dev container). Match the
        # Playwright image's baked non-root user (pwuser / uid 1000).
        if is_playwright and uid == 0:
            sandbox_uid, sandbox_gid = 1000, 1000
        else:
            sandbox_uid, sandbox_gid = uid, gid
        try:
            tarball = await asyncio.to_thread(
                build_source_tarball, cwd, uid=sandbox_uid, gid=sandbox_gid
            )
        except OSError as exc:
            raise RunnerError(f"Failed to package source directory: {exc}") from exc

        # 5. A fresh named volume backs the sandbox's writable workspace.
        # Named volumes are daemon-side storage independent of container
        # start/stop state (unlike tmpfs, which only exists while the
        # container's mount namespace is live) — put_archive/get_archive work
        # against it regardless of the container's running state.
        try:
            volume = await asyncio.to_thread(client.volumes.create)
        except Exception as exc:
            raise RunnerError(f"Docker volume creation failed: {exc}") from exc

        async def _kill_container(reason: str) -> None:
            try:
                await asyncio.to_thread(container.kill)
            except Exception as kill_exc:
                logger.warning("Failed to kill container (%s): %s", reason, kill_exc)

        container = None
        try:
            volumes = {volume.name: {"bind": _IN_CONTAINER_WORKDIR, "mode": "rw"}}
            for path, spec in extra_volumes.items():
                volumes.setdefault(path, spec)

            # 6. Create (not started yet) with a placeholder command — the
            # real command runs via exec after put_archive, since a
            # newly-created-but-not-started container's volume mount isn't
            # actually populated by put_archive until the container starts
            # (confirmed empirically; contradicts the container.create-then-
            # put_archive-then-start sequence one might otherwise expect).
            def _create_container():
                create_kwargs = dict(
                    command=["sleep", str(max(int(timeout) + 60, 60))],
                    volumes=volumes,
                    working_dir=_IN_CONTAINER_WORKDIR,
                    detach=True,
                    # SEC-3: execute untrusted test code with least privilege.
                    user=f"{sandbox_uid}:{sandbox_gid}",
                    network_mode="none",
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges"],
                    pids_limit=512,
                    mem_limit="2g",
                    nano_cpus=2_000_000_000,  # SEC-3: cap CPU at 2.0 cores.
                    # SEC-3: read-only root filesystem; the named volume above
                    # and this tmpfs are the only writable locations.
                    read_only=True,
                    tmpfs={"/tmp": ""},
                )
                if labels:
                    create_kwargs["labels"] = dict(labels)
                if is_playwright:
                    # T-M5-BROWSER-001 / DES §4.6: apply the formal browser
                    # sandbox profile (container-local shm, non-root, no
                    # privileged / SYS_ADMIN / host IPC) and fail closed if
                    # anything in the create kwargs violates it.
                    from qarunner.domain import (
                        BrowserSandboxProfile,
                        browser_container_create_kwargs,
                    )

                    browser_kwargs = browser_container_create_kwargs(
                        profile=BrowserSandboxProfile.m5_browser_default(),
                        user=f"{sandbox_uid}:{sandbox_gid}",
                    )
                    create_kwargs.update(browser_kwargs)
                return client.containers.create(image, **create_kwargs)

            container = await asyncio.to_thread(_create_container)
            await asyncio.to_thread(container.start)
            await asyncio.to_thread(container.put_archive, _IN_CONTAINER_WORKDIR, tarball)

            # 7. Run the real command via the low-level exec API (the
            # high-level container.exec_run()'s streamed form doesn't expose
            # the exec_id needed to retrieve an exit code once the stream is
            # drained, per docker-py's ExecResult shape).
            def _create_exec():
                return client.api.exec_create(
                    container.id,
                    exec_cmd,
                    environment=exec_env,
                    workdir=_IN_CONTAINER_WORKDIR,
                    stdout=True,
                    stderr=True,
                )["Id"]

            exec_id = await asyncio.to_thread(_create_exec)
            stream_task = asyncio.create_task(
                asyncio.to_thread(
                    _drain_exec_stream, client.api, exec_id, stdout_file, stderr_file
                )
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(stream_task, timeout=timeout)
                exit_code = await asyncio.to_thread(
                    lambda: client.api.exec_inspect(exec_id)["ExitCode"]
                )
                # The real command finished; stop the `sleep` placeholder.
                await _kill_container("post-exec cleanup")
            except TimeoutError:
                # This is an explicit asyncio-level deadline we impose, not a
                # daemon-reported signal — unlike an infra failure below,
                # elapsing it always means "the suite ran too long."
                timed_out = True
                logger.warning("Container execution timed out. Killing container...")
                await _kill_container("timeout")
                exit_code = 137  # Standard SIGKILL exit code
            except Exception as exec_exc:
                # BUG-10 carried into the exec model: any other exception
                # (daemon connection reset, API error) during exec is an
                # infrastructure failure, not "the suite ran too long", and
                # must not be reported as timed_out=True.
                logger.warning(
                    "Container exec failed (not a timeout): %s. Killing container...",
                    exec_exc,
                )
                await _kill_container("exec failure")
                exit_code = 137  # Standard SIGKILL exit code; state is unknown

            # 8. Pull results back out, if the command was expected to
            # produce any — silently skip if it never wrote them (crashed
            # before producing output), matching the old bind-mount behavior
            # where a missing file was simply absent.
            if host_results_dir:
                try:
                    bits, _stat = await asyncio.to_thread(
                        container.get_archive, _IN_CONTAINER_RESULTS_DIR
                    )
                    raw = b"".join(bits)
                    await asyncio.to_thread(extract_results_archive, raw, host_results_dir)
                except NotFound:
                    pass

        except Exception as exc:
            if not timed_out:
                raise RunnerError(f"Docker container execution failed: {exc}") from exc
        finally:
            if container:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(container.remove, force=True)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(volume.remove, force=True)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return ProcessResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            duration_ms=elapsed_ms,
            timed_out=timed_out,
        )
