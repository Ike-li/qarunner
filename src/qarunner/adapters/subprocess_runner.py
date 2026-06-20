"""Real process runner using asyncio subprocesses."""

from __future__ import annotations

import asyncio
import time

from qarunner.errors import RunnerError
from qarunner.models import ProcessResult


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
        import os
        start = time.monotonic()
        timed_out = False

        f_out = None
        f_err = None
        if stdout_file:
            os.makedirs(os.path.dirname(stdout_file), exist_ok=True)
            f_out = open(stdout_file, "wb")
        if stderr_file:
            os.makedirs(os.path.dirname(stderr_file), exist_ok=True)
            f_err = open(stderr_file, "wb")

        full_env = dict(os.environ)
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
            if f_out: f_out.close()
            if f_err: f_err.close()
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
            if f_out:
                f_out.close()
            if f_err:
                f_err.close()

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if stdout_file and os.path.exists(stdout_file):
            with open(stdout_file, "rb") as f:
                stdout_bytes = f.read()
        if stderr_file and os.path.exists(stderr_file):
            with open(stderr_file, "rb") as f:
                stderr_bytes = f.read()

        return ProcessResult(
            exit_code=proc.returncode,
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            duration_ms=elapsed_ms,
            timed_out=timed_out,
        )

