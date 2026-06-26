"""Real process runner using asyncio subprocesses."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time

from qarunner.errors import RunnerError
from qarunner.models import ProcessResult

# SEC-8: ProcessResult.stdout/stderr is never consumed downstream (the
# orchestrator only reads exit_code/timed_out; the real logs live in the on-disk
# stdout/stderr files). Reading a multi-GB log fully into memory here is pure
# waste and an OOM vector, so we keep only the tail.
_MAX_CAPTURE_BYTES = 256 * 1024

# SEC-3: untrusted test code must inherit only a minimal allowlist of host
# environment variables — never the platform's full environment, which routinely
# holds cloud credentials / tokens / DB URLs under arbitrary (non-QARUNNER_)
# names that a blocklist would miss. The allowlist is the functional minimum for
# pytest and the allure CLI (which share this runner) to locate
# interpreters/tools, resolve locale and temp dirs, and find the JVM.
# Caller-supplied env is layered on top.
_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "JAVA_HOME",  # the allure CLI runs on the JVM
    }
)


def _read_tail(path: str, limit: int) -> bytes:
    """Read at most the last *limit* bytes of *path* without loading it all."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > limit:
            f.seek(size - limit)
        return f.read()


class SubprocessRunner:
    """Execute external commands via asyncio.create_subprocess_exec."""

    async def run(
        self,
        cmd: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
        stdout_file: str | None = None,
        stderr_file: str | None = None,
    ) -> ProcessResult:
        start = time.monotonic()
        timed_out = False

        f_out = None
        f_err = None
        if stdout_file:
            os.makedirs(os.path.dirname(stdout_file), exist_ok=True)
            f_out = open(stdout_file, "wb")  # noqa: SIM115 (handle outlives this scope)
        if stderr_file:
            os.makedirs(os.path.dirname(stderr_file), exist_ok=True)
            f_err = open(stderr_file, "wb")  # noqa: SIM115 (handle outlives this scope)

        # SEC-3: build the child environment from a minimal allowlist of host
        # vars (see _ENV_ALLOWLIST) plus the caller-supplied env, so untrusted
        # test code never inherits platform secrets carried in arbitrary
        # (non-QARUNNER_) host variables. Caller-supplied env takes precedence.
        full_env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
        if env:
            full_env.update(env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=full_env,
                stdout=f_out or asyncio.subprocess.PIPE,
                stderr=f_err or asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            if f_out: f_out.close()  # noqa: E701
            if f_err: f_err.close()  # noqa: E701
            raise RunnerError(str(exc)) from exc

        stdout_bytes = b""
        stderr_bytes = b""

        try:
            if f_out or f_err:
                await asyncio.wait_for(
                    proc.wait(),
                    timeout=timeout,
                )
            else:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
        except TimeoutError:
            timed_out = True
            import signal
            try:
                proc.send_signal(signal.SIGINT)
            except (AttributeError, ValueError, NotImplementedError):
                proc.terminate()

            try:
                if f_out or f_err:
                    await asyncio.wait_for(
                        proc.wait(),
                        timeout=5.0,
                    )
                else:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=5.0,
                    )
            except TimeoutError:
                proc.terminate()
                try:
                    if f_out or f_err:
                        await asyncio.wait_for(
                            proc.wait(),
                            timeout=2.0,
                        )
                    else:
                        stdout_bytes, stderr_bytes = await asyncio.wait_for(
                            proc.communicate(),
                            timeout=2.0,
                        )
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                    if not (f_out or f_err):
                        stdout_bytes, stderr_bytes = await proc.communicate()
        finally:
            if proc.returncode is None:
                # Cancelled (e.g. shutdown drain past its deadline) before the
                # child finished: kill it so untrusted test code isn't left
                # running orphaned, outside lifecycle control.
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            if f_out:
                f_out.close()
            if f_err:
                f_err.close()

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if stdout_file and os.path.exists(stdout_file):
            stdout_bytes = _read_tail(stdout_file, _MAX_CAPTURE_BYTES)
        if stderr_file and os.path.exists(stderr_file):
            stderr_bytes = _read_tail(stderr_file, _MAX_CAPTURE_BYTES)

        return ProcessResult(
            exit_code=proc.returncode,
            stdout=stdout_bytes[-_MAX_CAPTURE_BYTES:].decode(errors="replace"),
            stderr=stderr_bytes[-_MAX_CAPTURE_BYTES:].decode(errors="replace"),
            duration_ms=elapsed_ms,
            timed_out=timed_out,
        )

