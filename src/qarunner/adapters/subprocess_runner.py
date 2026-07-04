"""Real process runner using asyncio subprocesses."""

from __future__ import annotations

import asyncio
import contextlib
import os
import resource
import sys
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

# SEC-3 H-4: POSIX resource limits applied via preexec_fn so untrusted test
# code can't exhaust CPU, memory, or fork-bomb the platform host.
_RLIMIT_CPU_HARD = 3600  # 1 hour
_RLIMIT_AS_HARD = 2 * 1024**3  # 2 GiB address space
_RLIMIT_NPROC_HARD = 128  # max child processes
_RLIMIT_FSIZE_HARD = 512 * 1024**2  # 512 MiB per file


def _set_subprocess_limits() -> None:  # pragma: no cover — runs in child process via preexec_fn
    """Set per-process resource limits (POSIX only, called via preexec_fn).

    Limits are applied *before* the child executable starts, so even a
    compromised or malicious test binary is confined.  Each limit is best-effort
    — if the current hard limit is lower than our target we keep the lower one
    (privilege cannot be escalated from inside the child).
    """
    try:
        # CPU time: cap at the action_timeout (typically 1800 s).  The
        # orchestrator also kills on wall-clock timeout, but the kernel
        # RLIMIT_CPU delivers SIGXCPU as a defence-in-depth layer.
        _best_effort_rlimit(resource.RLIMIT_CPU, _RLIMIT_CPU_HARD)
        # Virtual memory (address space): 2 GiB.
        _best_effort_rlimit(resource.RLIMIT_AS, _RLIMIT_AS_HARD)
        # Number of child processes: prevent fork bombs.
        _best_effort_rlimit(resource.RLIMIT_NPROC, _RLIMIT_NPROC_HARD)
        # File size: a single test must not write a multi-GB log.
        _best_effort_rlimit(resource.RLIMIT_FSIZE, _RLIMIT_FSIZE_HARD)
    except Exception:
        # preexec_fn runs in a restricted context (between fork and exec);
        # exceptions are fatal anyway, but an explicit handler avoids a
        # confusing traceback-less crash.
        pass


def _best_effort_rlimit(res: int, target: int) -> None:
    """Set the soft limit for *res* to *target*, bounded by the current hard
    limit (prevents escalation and avoids ``ValueError`` on the setrlimit call).
    """
    _soft, hard = resource.getrlimit(res)
    soft = target if hard == resource.RLIM_INFINITY else min(target, hard)
    resource.setrlimit(res, (soft, hard))


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
                **({"preexec_fn": _set_subprocess_limits} if sys.platform != "win32" else {}),
            )
        except OSError as exc:
            if f_out:
                f_out.close()  # noqa: E701
            if f_err:
                f_err.close()  # noqa: E701
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
