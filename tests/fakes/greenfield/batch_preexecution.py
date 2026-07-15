"""Deterministic Fake for Batch pre-execution application slices."""

from datetime import UTC, datetime

from qarunner.application.handoff import BatchMaterializedScopeHandoff
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionStamp,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
    BatchCancellationAuthority,
    BatchCancellationSideEffect,
    BatchClosureAuthority,
    BatchClosureSideEffect,
    BatchRejectionAuthority,
    BatchRejectionSideEffect,
    InternalAuthorityRetired,
)
from qarunner.application.ports.common import ReplayResult
from qarunner.domain.batch import Batch, BatchRejection, BatchRejectionStage, BatchState
from qarunner.domain.cancellation import (
    BatchCancellationIntent,
    BatchCancellationScope,
    BatchCancellationScopeKind,
    CancellationSource,
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
        current_closure_epoch: int = 1,
        current_rejection_epoch: int = 1,
        rejection_source_batch_version: int | None = None,
        closure_authority_current: bool = True,
        rejection_authority_current: bool = True,
        authority_checked_at: datetime = datetime(2026, 7, 15, 6, tzinfo=UTC),
        authority_expires_at: datetime = datetime(2026, 7, 16, 6, tzinfo=UTC),
        authority_projection_version: int = 7,
        authority_revocation_watermark: int = 11,
        required_projection_version: int = 7,
        required_revocation_watermark: int = 11,
        authority_scope: BatchCancellationScope | None = None,
        cancel_actor_id: str = "user-001",
        cancel_source: CancellationSource = CancellationSource.USER_REQUEST,
        cancel_registered_service: bool = False,
        cancel_policy_allowed: bool = True,
    ) -> None:
        self.batch = batch
        self.authority_available = authority_available
        self.authority_allowed = authority_allowed
        self.publication_error = publication_error
        self.current_closure_epoch = current_closure_epoch
        self.current_rejection_epoch = current_rejection_epoch
        self.rejection_source_batch_version = rejection_source_batch_version
        self.closure_authority_current = closure_authority_current
        self.rejection_authority_current = rejection_authority_current
        self.authority_checked_at = authority_checked_at
        self.authority_expires_at = authority_expires_at
        self.authority_projection_version = authority_projection_version
        self.authority_revocation_watermark = authority_revocation_watermark
        self.required_projection_version = required_projection_version
        self.required_revocation_watermark = required_revocation_watermark
        self.cancel_registered_service = cancel_registered_service
        self.cancel_policy_allowed = cancel_policy_allowed
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
        projection = self._projection_stamp()
        authorization_payload: dict[str, object] = {"label": "cancel-authorization"}
        if (
            cancel_actor_id != "user-001"
            or cancel_source is not CancellationSource.USER_REQUEST
            or cancel_registered_service
            or not cancel_policy_allowed
        ):
            authorization_payload.update(
                {
                    "actor_id": cancel_actor_id,
                    "source": cancel_source.value,
                    "registered_service": cancel_registered_service,
                    "policy_allowed": cancel_policy_allowed,
                }
            )
        self._authority = BatchCancellationAuthority(
            batch_id=batch.id,
            project_id="project-001",
            suite_revision_id="suite-revision-001",
            actor_id=cancel_actor_id,
            source=cancel_source,
            authorization_digest=canonical_digest(
                schema_version="qep.test-batch-cancel.v1",
                payload=authorization_payload,
            ),
            scope=(
                authority_scope
                if authority_scope is not None
                else BatchCancellationScope(
                    kind=BatchCancellationScopeKind.PRE_PLAN,
                    preplan_scope_digest=canonical_digest(
                        schema_version="qep.test-batch-cancel.v1",
                        payload={"label": "preplan-scope"},
                    ),
                    manifest_digest=None,
                    shard_plan_version=None,
                    shard_plan_digest=None,
                    canonical_run_set_digest=None,
                )
            ),
            projection=projection,
            recorded_at=datetime(2026, 7, 14, 6, tzinfo=UTC),
        )
        self.last_cancel_authority = self._authority

    async def require_cancel_authority(self, *, batch_id: str) -> BatchCancellationAuthority:
        self.authority_checks += 1
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
        self._require_current_projection()
        if self.authority_expires_at <= self.authority_checked_at:
            raise AuthorityProjectionUnavailable
        if not self.cancel_policy_allowed:
            raise AuthorityPermissionDenied
        if self._authority.source in {
            CancellationSource.DEADLINE_EXCEEDED,
            CancellationSource.POLICY_ENFORCEMENT,
        } and not self.cancel_registered_service:
            raise AuthorityPermissionDenied
        if not self.authority_allowed:
            raise AuthorityPermissionDenied
        if batch_id != self._authority.batch_id:
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
        if not self.authority_available:
            raise AuthorityProjectionUnavailable
        self._require_current_projection()
        if self.authority_expires_at <= self.authority_checked_at:
            raise AuthorityProjectionUnavailable
        if not self.closure_authority_current:
            raise InternalAuthorityRetired(reason="reconciler_authority_retired")
        if not self.authority_allowed:
            raise AuthorityPermissionDenied
        if closure_epoch != self.current_closure_epoch:
            raise AuthorityStateConflict(reason="closure_epoch_superseded")
        intent = self.batch.cancellation_intent
        if intent is not None and self._authority.scope != intent.scope:
            raise AuthorityStateConflict(reason="source_binding_superseded")
        projection = self._projection_stamp()
        return BatchClosureAuthority(
            batch_id=batch_id,
            project_id="project-001" if intent is None else intent.project_id,
            suite_revision_id=(
                self._authority.suite_revision_id if intent is None else intent.suite_revision_id
            ),
            source_batch_version=(
                self.batch.version if intent is None else intent.source_batch_version + 1
            ),
            authority_digest=canonical_digest(
                schema_version="qep.test-closure-authority.v1",
                payload={
                    "batch_id": batch_id,
                    "reconciler_id": reconciler_id,
                    "projection_version": projection.projection_version,
                    "revocation_watermark": projection.revocation_watermark,
                    "write_epoch": self.current_closure_epoch,
                },
            ),
            scope=self._authority.scope if intent is None else intent.scope,
            projection=projection,
            write_epoch=self.current_closure_epoch,
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
        self._require_current_projection()
        if self.authority_expires_at <= self.authority_checked_at:
            raise AuthorityProjectionUnavailable
        if not self.rejection_authority_current:
            raise InternalAuthorityRetired(reason="phase_owner_authority_retired")
        if not self.authority_allowed:
            raise AuthorityPermissionDenied
        if batch_id != self.batch.id:
            raise AuthorityPermissionDenied
        if rejection_epoch != self.current_rejection_epoch:
            raise AuthorityStateConflict(reason="phase_epoch_superseded")
        stored = self.batch.rejection_fact
        if stored is None and self.batch.state not in {
            BatchState.VALIDATING,
            BatchState.COLLECTING,
            BatchState.PLANNING,
            BatchState.AWAITING_ADMISSION,
        }:
            raise AuthorityStateConflict(reason="rejection_phase_not_current")
        current_source_version = (
            self.batch.version if stored is None else stored.source_batch_version
        )
        if self.rejection_source_batch_version is not None:
            current_source_version = self.rejection_source_batch_version
        projection = self._projection_stamp()
        stage = (
            {
                BatchState.VALIDATING: BatchRejectionStage.VALIDATION,
                BatchState.COLLECTING: BatchRejectionStage.COLLECTION,
                BatchState.PLANNING: BatchRejectionStage.PLANNING,
                BatchState.AWAITING_ADMISSION: BatchRejectionStage.ADMISSION,
            }[self.batch.state]
            if stored is None
            else stored.stage
        )
        authority_digest = (
            canonical_digest(
                schema_version="qep.test-rejection-authority.v1",
                payload={
                    "batch_id": batch_id,
                    "phase_owner_id": phase_owner_id,
                    "projection_version": projection.projection_version,
                    "revocation_watermark": projection.revocation_watermark,
                    "write_epoch": self.current_rejection_epoch,
                },
            )
            if stored is None
            else stored.authority_digest
        )
        assert authority_digest is not None
        authority = BatchRejectionAuthority(
            batch_id=batch_id,
            project_id="project-001",
            suite_revision_id=self._authority.suite_revision_id,
            source_batch_version=current_source_version,
            stage=stage,
            authority_digest=authority_digest,
            scope=self._authority.scope,
            projection=projection,
            recorded_at=self.authority_checked_at if stored is None else stored.recorded_at,
            write_epoch=self.current_rejection_epoch,
        )
        self.last_rejection_authority = authority
        return authority

    def _projection_stamp(self) -> AuthorityProjectionStamp:
        return AuthorityProjectionStamp(
            source="local-authority-projection",
            projection_version=self.authority_projection_version,
            revocation_watermark=self.authority_revocation_watermark,
            expires_at=self.authority_expires_at,
        )

    def _require_current_projection(self) -> None:
        if (
            self.authority_projection_version < self.required_projection_version
            or self.authority_revocation_watermark < self.required_revocation_watermark
        ):
            raise AuthorityProjectionUnavailable

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
