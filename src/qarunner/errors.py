"""Domain exceptions for qarunner."""


class RunNotFound(KeyError):
    """Raised when a run ID does not exist in the store."""


class UnknownRunner(ValueError):
    """Raised when a requested runner name is not registered."""


class UnsafePath(ValueError):
    """Raised when a resolved path escapes the allowed root."""


class UnsafeArguments(UnsafePath):
    """Raised when run arguments contain dangerous pytest flags or unsafe file selectors.

    Subclasses ``UnsafePath`` so existing ``except UnsafePath`` handlers
    (e.g. in the API layer) translate it to HTTP 400 without changes.
    """


class UnsupportedExecutor(ValueError):
    """Raised when a runner cannot run on the requested executor.

    The bundled docker executor image is python-only, so the ``playwright``
    runner needs ``executor_mode='subprocess'`` (or a playwright-capable image).
    A domain-level invariant so every caller — the API route *and* the scheduler,
    which reaches ``create`` directly — is protected, not just the route.
    """


class RunnerError(RuntimeError):
    """Raised when a runner fails to build a command or execute."""


class ScheduleNotFound(KeyError):
    """Raised when a schedule ID does not exist in the store."""


class ProfileNotFound(KeyError):
    """Raised when a profile ID does not exist in the store."""


class InvalidScheduleRequest(ValueError):
    """Raised when a schedule create/update request fails validation.

    Covers an unknown profile reference, an invalid timezone, or an invalid
    cron expression. The message is suitable for surfacing as an HTTP 400.
    """


class LoginLockedOut(Exception):
    """Raised when a login identity is temporarily locked after repeated failures.

    Brute-force protection (SEC-5). Carries ``retry_after`` (whole seconds) so
    the API layer can answer HTTP 429 with a ``Retry-After`` header.
    """

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"too many failed login attempts; retry after {retry_after}s")
        self.retry_after = retry_after
