"""Deterministic Fake for Batch pre-execution application slices."""

from datetime import UTC, datetime

from qarunner.application.handoff import BatchMaterializedScopeHandoff
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    BatchCancellationAuthority,
    BatchCancellationSideEffect,
    BatchClosureAuthority,
    BatchClosureSideEffect,
    BatchRejectionAuthority,
    BatchRejectionSideEffect,
)
from qarunner.application.ports.common import ReplayResult
from qarunner.domain.batch import Batch, BatchRejection
from qarunner.domain.cancellation import (
    BatchCancellationIntent,
    BatchCancellationScope,
    BatchCancellationScopeKind,
)
from qarunner.domain.digest import Digest, canonical_digest
from qarunner.domain.errors import IdempotencyConflict


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
        self.closure_authority_checks = 0
        self.rejection_authority_checks = 0
        self.batch_reads = 0
        self.audit_records: tuple[
            BatchCancellationSideEffect
            | BatchClosureSideEffect
            | BatchRejectionSideEffect
            | BatchMaterializedScopeHandoff,
            ...,
        ] = ()
        self.semantic_outbox: tuple[
            BatchCancellationSideEffect
            | BatchClosureSideEffect
            | BatchRejectionSideEffect
            | BatchMaterializedScopeHandoff,
            ...,
        ] = ()
        self.handoffs: dict[Digest, BatchMaterializedScopeHandoff] = {}
        self.run_resolutions: tuple[object, ...] = ()
        self.fanout_results: tuple[object, ...] = ()
        self.not_executed_facts: tuple[object, ...] = ()
        self.run_outcomes: tuple[object, ...] = ()
        self.quarantined_handoffs: tuple[Digest, ...] = ()
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

    async def require_closure_authority(
        self,
        *,
        batch_id: str,
        reconciler_id: str,
        closure_epoch: int,
    ) -> BatchClosureAuthority:
        self.closure_authority_checks += 1
        return BatchClosureAuthority(
            authority_digest=canonical_digest(
                schema_version="qep.test-closure-authority.v1",
                payload={"batch_id": batch_id, "reconciler_id": reconciler_id},
            ),
            write_epoch=closure_epoch,
        )

    async def publish_materialized_handoff(
        self, *, handoff: BatchMaterializedScopeHandoff
    ) -> ReplayResult[BatchMaterializedScopeHandoff]:
        stored = self.handoffs.get(handoff.semantic_trigger_key)
        if stored is not None:
            if stored.handoff_digest != handoff.handoff_digest:
                self.quarantined_handoffs += (handoff.semantic_trigger_key,)
                raise IdempotencyConflict(
                    scope=f"batch:{handoff.batch_id}:materialized_handoff",
                    key=handoff.semantic_trigger_key.value,
                    stored_digest=stored.handoff_digest,
                    received_digest=handoff.handoff_digest,
                )
            return ReplayResult(value=stored, replayed=True)
        if self.publication_error is not None:
            raise self.publication_error
        self.handoffs[handoff.semantic_trigger_key] = handoff
        self.audit_records += (handoff,)
        self.semantic_outbox += (handoff,)
        return ReplayResult(value=handoff, replayed=False)

    async def require_rejection_authority(
        self,
        *,
        batch_id: str,
        phase_owner_id: str,
        rejection_epoch: int,
    ) -> BatchRejectionAuthority:
        self.rejection_authority_checks += 1
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
        if not self.authority_allowed:
            raise AuthorityPermissionDenied
        return BatchRejectionAuthority(
            project_id="project-001",
            suite_revision_id=self._authority.suite_revision_id,
            authority_digest=canonical_digest(
                schema_version="qep.test-rejection-authority.v1",
                payload={"batch_id": batch_id, "phase_owner_id": phase_owner_id},
            ),
            scope=self._authority.scope,
            write_epoch=rejection_epoch,
        )

    async def publish_preexecution_closure(self, *, batch: Batch) -> None:
        assert batch.preexecution_closure_basis is not None
        if self.batch is batch:
            return
        side_effect = BatchClosureSideEffect(
            batch_id=batch.id,
            basis_digest=batch.preexecution_closure_basis.digest,
        )
        self.batch = batch
        self.audit_records += (side_effect,)
        self.semantic_outbox += (side_effect,)

    async def publish_preexecution_rejection(
        self, *, batch: Batch, rejection: BatchRejection
    ) -> None:
        assert batch.preexecution_closure_basis is not None
        if self.publication_error is not None:
            raise self.publication_error
        side_effect = BatchRejectionSideEffect(
            batch_id=batch.id,
            rejection_digest=rejection.digest,
            basis_digest=batch.preexecution_closure_basis.digest,
        )
        self.batch = batch
        self.audit_records += (side_effect,)
        self.semantic_outbox += (side_effect,)
