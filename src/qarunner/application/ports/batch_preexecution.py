"""Ports for Batch-owned pre-execution commands."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from qarunner.domain.batch import Batch
from qarunner.domain.cancellation import BatchCancellationIntent, BatchCancellationScope
from qarunner.domain.digest import Digest


class AuthorityProjectionUnavailable(RuntimeError):
    """Current cancellation authority cannot be established safely."""


class AuthorityPermissionDenied(RuntimeError):
    """The current caller has no permission on the requested object."""


@dataclass(frozen=True, slots=True)
class BatchCancellationAuthority:
    """Server-derived facts required to materialize a cancellation intent."""

    suite_revision_id: str
    authorization_digest: Digest
    scope: BatchCancellationScope
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class BatchCancellationSideEffect:
    """Minimal identity shared by transactional audit and semantic delivery."""

    batch_id: str
    intent_digest: Digest


@runtime_checkable
class BatchPreexecutionGateway(Protocol):
    """Narrow authority and persistence boundary for pre-execution Batch work."""

    async def require_cancel_authority(
        self, *, project_id: str, actor_id: str
    ) -> BatchCancellationAuthority:
        """Require live authority without exposing any stored command identity."""

    async def get_batch_for_update(self, *, batch_id: str) -> Batch:
        """Return the authoritative Batch snapshot inside the command boundary."""

    async def publish_cancellation(self, *, batch: Batch, intent: BatchCancellationIntent) -> None:
        """Atomically publish Batch, audit, and semantic outbox facts."""
