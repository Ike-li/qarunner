"""Domain exceptions for qarunner."""


class RunNotFound(KeyError):
    """Raised when a run ID does not exist in the store."""


class UnknownRunner(ValueError):
    """Raised when a requested runner name is not registered."""


class UnsafePath(ValueError):
    """Raised when a resolved path escapes the allowed root."""


class RunnerError(RuntimeError):
    """Raised when a runner fails to build a command or execute."""
