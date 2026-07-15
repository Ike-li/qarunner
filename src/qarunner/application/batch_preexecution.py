"""Application commands for Batch-owned pre-execution convergence."""

from __future__ import annotations

from dataclasses import dataclass

from qarunner.application.ports.batch_preexecution import (
    AuthorityProjectionUnavailable,
    BatchPreexecutionGateway,
)
from qarunner.domain.cancellation import CancellationSource


class TemporarilyUnavailable(RuntimeError):
    """Safe public problem used when current authority cannot be established."""

    code = "TEMPORARILY_UNAVAILABLE"
    retryable = True
    http_status = 503

    def __init__(self) -> None:
        super().__init__("the operation is temporarily unavailable")


@dataclass(frozen=True, slots=True)
class RequestBatchCancellationCommand:
    """Caller-controlled identity for a Batch cancellation request."""

    batch_id: str
    project_id: str
    expected_batch_version: int
    idempotency_key: str
    source: CancellationSource
    actor_id: str
    reason: str


class RequestBatchCancellation:
    """Validate live authority before reading replayable Batch state."""

    def __init__(self, *, gateway: BatchPreexecutionGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: RequestBatchCancellationCommand) -> None:
        try:
            await self._gateway.require_cancel_authority(
                project_id=command.project_id,
                actor_id=command.actor_id,
            )
        except AuthorityProjectionUnavailable:
            raise TemporarilyUnavailable() from None
