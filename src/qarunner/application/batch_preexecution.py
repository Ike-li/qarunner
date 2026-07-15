"""Application commands for Batch-owned pre-execution convergence."""

from __future__ import annotations

from dataclasses import dataclass

from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    BatchPreexecutionGateway,
)
from qarunner.domain.cancellation import BatchCancellationIntent, CancellationSource
from qarunner.domain.digest import canonical_digest
from qarunner.domain.errors import BatchCancellationConflict, IdempotencyConflict


class TemporarilyUnavailable(RuntimeError):
    """Safe public problem used when current authority cannot be established."""

    code = "TEMPORARILY_UNAVAILABLE"
    retryable = True
    http_status = 503

    def __init__(self) -> None:
        super().__init__("the operation is temporarily unavailable")


class ObjectForbidden(RuntimeError):
    """Safe public problem for missing current object permission."""

    code = "OBJECT_FORBIDDEN"
    retryable = False
    http_status = 403

    def __init__(self) -> None:
        super().__init__("the operation is forbidden")


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

    async def execute(self, command: RequestBatchCancellationCommand) -> BatchCancellationIntent:
        try:
            authority = await self._gateway.require_cancel_authority(
                project_id=command.project_id,
                actor_id=command.actor_id,
            )
        except AuthorityProjectionUnavailable:
            raise TemporarilyUnavailable() from None
        except AuthorityPermissionDenied:
            raise ObjectForbidden() from None
        batch = await self._gateway.get_batch_for_update(batch_id=command.batch_id)
        stored = batch.cancellation_intent
        received_digest = canonical_digest(
            schema_version="qep.batch-cancellation-request.v1",
            payload={
                "batch_id": command.batch_id,
                "source_batch_version": command.expected_batch_version,
                "idempotency_key": command.idempotency_key,
                "source": command.source.value,
                "actor_id": command.actor_id,
                "reason": command.reason,
            },
        )
        if (
            stored is not None
            and stored.idempotency_key == command.idempotency_key
            and stored.request_digest != received_digest
        ):
            raise IdempotencyConflict(
                scope=f"batch:{command.batch_id}:cancel",
                key=command.idempotency_key,
                stored_digest=stored.request_digest,
                received_digest=received_digest,
            )
        if stored is not None and stored.idempotency_key != command.idempotency_key:
            raise BatchCancellationConflict(
                batch_id=command.batch_id,
                stored_key=stored.idempotency_key,
                received_key=command.idempotency_key,
            )
        if stored is not None and (
            stored.batch_id == command.batch_id
            and stored.project_id == command.project_id
            and stored.source_batch_version == command.expected_batch_version
            and stored.idempotency_key == command.idempotency_key
            and stored.source is command.source
            and stored.actor_id == command.actor_id
            and stored.reason == command.reason
        ):
            return stored
        intent = BatchCancellationIntent(
            batch_id=command.batch_id,
            project_id=command.project_id,
            suite_revision_id=authority.suite_revision_id,
            source_batch_version=command.expected_batch_version,
            idempotency_key=command.idempotency_key,
            source=command.source,
            actor_id=command.actor_id,
            reason=command.reason,
            authorization_digest=authority.authorization_digest,
            scope=authority.scope,
            recorded_at=authority.recorded_at,
        )
        requested = batch.request_cancel(
            intent=intent,
            expected_version=command.expected_batch_version,
        )
        await self._gateway.publish_cancellation(batch=requested, intent=intent)
        return intent
