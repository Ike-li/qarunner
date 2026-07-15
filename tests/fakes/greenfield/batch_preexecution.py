"""Deterministic Fake for Batch pre-execution application slices."""

from datetime import UTC, datetime

from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    BatchCancellationAuthority,
    BatchCancellationSideEffect,
)
from qarunner.domain.batch import Batch
from qarunner.domain.cancellation import (
    BatchCancellationIntent,
    BatchCancellationScope,
    BatchCancellationScopeKind,
)
from qarunner.domain.digest import canonical_digest


class InMemoryBatchPreexecutionGateway:
    """Expose command ordering and durable state without simulating production I/O."""

    def __init__(
        self,
        *,
        batch: Batch,
        authority_available: bool = True,
        authority_allowed: bool = True,
        publication_error: Exception | None = None,
    ) -> None:
        self.batch = batch
        self.authority_available = authority_available
        self.authority_allowed = authority_allowed
        self.publication_error = publication_error
        self.authority_checks = 0
        self.batch_reads = 0
        self.audit_records: tuple[BatchCancellationSideEffect, ...] = ()
        self.semantic_outbox: tuple[BatchCancellationSideEffect, ...] = ()
        self._authority = BatchCancellationAuthority(
            suite_revision_id="suite-revision-001",
            authorization_digest=canonical_digest(
                schema_version="qep.test-batch-cancel.v1",
                payload={"label": "cancel-authorization"},
            ),
            scope=BatchCancellationScope(
                kind=BatchCancellationScopeKind.PRE_PLAN,
                preplan_scope_digest=canonical_digest(
                    schema_version="qep.test-batch-cancel.v1",
                    payload={"label": "preplan-scope"},
                ),
                manifest_digest=None,
                shard_plan_version=None,
                shard_plan_digest=None,
                canonical_run_set_digest=None,
            ),
            recorded_at=datetime(2026, 7, 14, 6, tzinfo=UTC),
        )

    async def require_cancel_authority(
        self, *, project_id: str, actor_id: str
    ) -> BatchCancellationAuthority:
        del project_id, actor_id
        self.authority_checks += 1
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
        if not self.authority_allowed:
            raise AuthorityPermissionDenied
        return self._authority

    async def get_batch_for_update(self, *, batch_id: str) -> Batch:
        self.batch_reads += 1
        if batch_id != self.batch.id:
            raise KeyError(batch_id)
        return self.batch

    async def publish_cancellation(self, *, batch: Batch, intent: BatchCancellationIntent) -> None:
        if self.publication_error is not None:
            raise self.publication_error
        side_effect = BatchCancellationSideEffect(
            batch_id=batch.id,
            intent_digest=intent.digest,
        )
        self.batch = batch
        self.audit_records += (side_effect,)
        self.semantic_outbox += (side_effect,)
