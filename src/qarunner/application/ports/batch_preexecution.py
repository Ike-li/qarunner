"""Ports for Batch-owned pre-execution commands."""

from typing import Protocol, runtime_checkable


class AuthorityProjectionUnavailable(RuntimeError):
    """Current cancellation authority cannot be established safely."""


@runtime_checkable
class BatchPreexecutionGateway(Protocol):
    """Narrow authority and persistence boundary for pre-execution Batch work."""

    async def require_cancel_authority(self, *, project_id: str, actor_id: str) -> None:
        """Require live authority without exposing any stored command identity."""
