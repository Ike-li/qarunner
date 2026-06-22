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


class RunnerError(RuntimeError):
    """Raised when a runner fails to build a command or execute."""
