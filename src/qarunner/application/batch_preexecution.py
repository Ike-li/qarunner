"""Application commands for Batch-owned pre-execution convergence."""

from __future__ import annotations

from dataclasses import dataclass

from qarunner.application.handoff import build_cancel_handoff, build_rejection_conflict_handoff
from qarunner.application.ports.batch_preexecution import (
    AuthorityPermissionDenied,
    AuthorityProjectionUnavailable,
    AuthorityStateConflict,
    BatchPreexecutionGateway,
    InternalAuthorityRetired,
)
from qarunner.application.preexecution_proof import (
    MaterializedExecutionScope,
    ProvePreexecutionClosure,
    ProvePreexecutionClosureCommand,
)
from qarunner.domain.batch import (
    BatchPreexecutionTerminalKind,
    BatchRejection,
    BatchRejectionReasonClass,
)
from qarunner.domain.cancellation import BatchCancellationIntent
from qarunner.domain.digest import Digest, canonical_digest
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


class PreexecutionStateConflict(RuntimeError):
    """Safe internal problem for superseded phase or reconciler authority."""

    code = "STATE_CONFLICT"
    retryable = False
    http_status = 409

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__("the operation conflicts with current pre-execution authority")


@dataclass(frozen=True, slots=True)
class RequestBatchCancellationCommand:
    """Caller-controlled identity for a Batch cancellation request."""

    batch_id: str
    expected_batch_version: int
    idempotency_key: str
    reason: str


class RequestBatchCancellation:
    """Validate live authority before reading replayable Batch state."""

    def __init__(self, *, gateway: BatchPreexecutionGateway) -> None:
        self._gateway = gateway

    async def execute(self, command: RequestBatchCancellationCommand) -> BatchCancellationIntent:
        try:
            authority = await self._gateway.require_cancel_authority(batch_id=command.batch_id)
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
                "source": authority.source.value,
                "actor_id": authority.actor_id,
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
            stored.batch_id != authority.batch_id
            or stored.project_id != authority.project_id
            or stored.suite_revision_id != authority.suite_revision_id
            or stored.source is not authority.source
            or stored.actor_id != authority.actor_id
            or stored.authorization_digest != authority.authorization_digest
            or stored.scope != authority.scope
        ):
            raise PreexecutionStateConflict(reason="cancel_authority_binding_superseded")
        if stored is not None and (
            stored.batch_id == command.batch_id
            and stored.project_id == authority.project_id
            and stored.source_batch_version == command.expected_batch_version
            and stored.idempotency_key == command.idempotency_key
            and stored.source is authority.source
            and stored.actor_id == authority.actor_id
            and stored.reason == command.reason
        ):
            return stored
        intent = BatchCancellationIntent(
            batch_id=command.batch_id,
            project_id=authority.project_id,
            suite_revision_id=authority.suite_revision_id,
            source_batch_version=command.expected_batch_version,
            idempotency_key=command.idempotency_key,
            source=authority.source,
            actor_id=authority.actor_id,
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


@dataclass(frozen=True, slots=True)
class ReconcilePreexecutionCancellationCommand:
    batch_id: str
    project_id: str
    suite_revision_id: str
    expected_batch_version: int
    reconciler_id: str
    closure_epoch: int


class ReconcilePreexecutionCancellation:
    """Close a pending intent only from current authority and zero-child proof."""

    def __init__(
        self,
        *,
        gateway: BatchPreexecutionGateway,
        proof: ProvePreexecutionClosure,
    ) -> None:
        self._gateway = gateway
        self._proof = proof

    async def execute(self, command: ReconcilePreexecutionCancellationCommand):
        try:
            authority = await self._gateway.require_closure_authority(
                batch_id=command.batch_id,
                reconciler_id=command.reconciler_id,
                closure_epoch=command.closure_epoch,
                project_id=command.project_id,
                suite_revision_id=command.suite_revision_id,
                source_batch_version=command.expected_batch_version,
            )
        except AuthorityProjectionUnavailable:
            raise TemporarilyUnavailable() from None
        except AuthorityPermissionDenied:
            raise ObjectForbidden() from None
        except InternalAuthorityRetired as error:
            raise PreexecutionStateConflict(reason=error.reason) from None
        except AuthorityStateConflict as error:
            raise PreexecutionStateConflict(reason=error.reason) from None
        batch = await self._gateway.get_batch_for_update(batch_id=command.batch_id)
        proof = await self._proof.execute(
            ProvePreexecutionClosureCommand(
                batch_id=command.batch_id,
                project_id=authority.project_id,
                suite_revision_id=authority.suite_revision_id,
                source_batch_version=authority.source_batch_version,
                scope=authority.scope,
                terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
                command_digest=batch.cancellation_intent.digest
                if batch.cancellation_intent
                else None,
            )
        )
        if isinstance(proof, MaterializedExecutionScope):
            intent = batch.cancellation_intent
            if intent is None:
                raise RuntimeError("materialized cancellation reconciliation requires intent")
            handoff = build_cancel_handoff(
                intent=intent,
                source_batch_version=command.expected_batch_version,
                authoritative_run_set_digest=proof.authoritative_run_set_digest,
                authority_digest=authority.authority_digest,
                write_epoch=authority.write_epoch,
            )
            published = await self._gateway.publish_materialized_handoff(handoff=handoff)
            return published.value
        closed = batch.finalize_unmaterialized_cancel(
            snapshot=proof,
            expected_version=command.expected_batch_version,
        )
        await self._gateway.publish_preexecution_closure(batch=closed)
        return closed


@dataclass(frozen=True, slots=True)
class RecordPreexecutionRejectionCommand:
    batch_id: str
    rejection_id: str
    reason_class: BatchRejectionReasonClass
    reason_code: str
    input_digest: Digest
    phase_owner_id: str
    rejection_epoch: int


class RejectionMaterializedConflict(RuntimeError):
    """The rejection was handed to execution reconciliation instead of closing Batch."""

    code = "STATE_CONFLICT"
    retryable = False

    def __init__(self, *, handoff) -> None:
        self.handoff = handoff
        super().__init__("the rejection conflicts with materialized execution scope")


class RecordPreexecutionRejection:
    """Record a rejection terminal or hand off a materialized-scope conflict."""

    def __init__(
        self,
        *,
        gateway: BatchPreexecutionGateway,
        proof: ProvePreexecutionClosure,
    ) -> None:
        self._gateway = gateway
        self._proof = proof

    async def execute(self, command: RecordPreexecutionRejectionCommand):
        try:
            authority = await self._gateway.require_rejection_authority(
                batch_id=command.batch_id,
                phase_owner_id=command.phase_owner_id,
                rejection_epoch=command.rejection_epoch,
            )
        except AuthorityProjectionUnavailable:
            raise TemporarilyUnavailable() from None
        except AuthorityPermissionDenied:
            raise ObjectForbidden() from None
        except InternalAuthorityRetired as error:
            raise PreexecutionStateConflict(reason=error.reason) from None
        except AuthorityStateConflict as error:
            raise PreexecutionStateConflict(reason=error.reason) from None
        rejection = BatchRejection(
            rejection_id=command.rejection_id,
            batch_id=authority.batch_id,
            source_batch_version=authority.source_batch_version,
            stage=authority.stage,
            reason_class=command.reason_class,
            reason_code=command.reason_code,
            input_digest=command.input_digest,
            authority_digest=authority.authority_digest,
            recorded_at=authority.recorded_at,
        )
        batch = await self._gateway.get_batch_for_update(batch_id=authority.batch_id)
        stored = batch.rejection_fact
        if stored is not None and stored.rejection_id == rejection.rejection_id:
            if stored.digest != rejection.digest:
                raise IdempotencyConflict(
                    scope=f"batch:{batch.id}:rejection",
                    key=stored.rejection_id,
                    stored_digest=stored.digest,
                    received_digest=rejection.digest,
                )
            basis = batch.preexecution_closure_basis
            if basis is None or not _basis_matches_authority_scope(
                basis=basis,
                scope=authority.scope,
            ):
                raise PreexecutionStateConflict(reason="rejection_authority_binding_superseded")
            return batch
        proof = await self._proof.execute(
            ProvePreexecutionClosureCommand(
                batch_id=authority.batch_id,
                project_id=authority.project_id,
                suite_revision_id=authority.suite_revision_id,
                source_batch_version=authority.source_batch_version,
                scope=authority.scope,
                terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
                command_digest=rejection.digest,
            )
        )
        if isinstance(proof, MaterializedExecutionScope):
            handoff = build_rejection_conflict_handoff(
                rejection=rejection,
                project_id=authority.project_id,
                suite_revision_id=authority.suite_revision_id,
                preplan_scope_digest=authority.scope.preplan_scope_digest,
                manifest_digest=authority.scope.manifest_digest,
                shard_plan_version=authority.scope.shard_plan_version,
                shard_plan_digest=authority.scope.shard_plan_digest,
                authoritative_run_set_digest=proof.authoritative_run_set_digest,
                authority_digest=authority.authority_digest,
                write_epoch=authority.write_epoch,
            )
            published = await self._gateway.publish_materialized_handoff(handoff=handoff)
            raise RejectionMaterializedConflict(handoff=published.value)
        closed = batch.reject_preexecution(
            rejection=rejection,
            snapshot=proof,
            expected_version=authority.source_batch_version,
        )
        await self._gateway.publish_preexecution_rejection(
            batch=closed,
            rejection=rejection,
        )
        return closed


def _basis_matches_authority_scope(*, basis, scope) -> bool:
    return (
        basis.scope_kind.value == scope.kind.value
        and basis.preplan_scope_digest == scope.preplan_scope_digest
        and basis.manifest_digest == scope.manifest_digest
        and basis.shard_plan_version == scope.shard_plan_version
        and basis.shard_plan_digest == scope.shard_plan_digest
        and basis.canonical_run_set_digest == scope.canonical_run_set_digest
    )
