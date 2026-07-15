"""Application closure of an authorized zero-child Batch cancellation."""

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_verified_zero_child_proof_atomically_closes_cancelled_batch() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import (
        Batch,
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchState,
        CancellationSource,
        canonical_digest,
    )

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = BatchCancellationIntent(
        batch_id=source.id,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=source.version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
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
        recorded_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    state = InMemoryBatchPreexecutionGateway(batch=requested)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)
    handler = ReconcilePreexecutionCancellation(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    )

    closed = await handler.execute(
        ReconcilePreexecutionCancellationCommand(
            batch_id=source.id,
            project_id=intent.project_id,
            suite_revision_id=intent.suite_revision_id,
            expected_batch_version=requested.version,
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
    )

    assert closed is state.batch
    assert closed.state is BatchState.CANCELLED
    assert closed.version == 5
    assert closed.preexecution_closure_basis is not None
    assert state.closure_authority_checks == 1
    assert proof_gateway.snapshot_assemblies == 1
    assert len(state.audit_records) == 1
    assert state.audit_records[0].basis_digest == closed.preexecution_closure_basis.digest
    assert len(state.semantic_outbox) == 1
    assert state.semantic_outbox[0].basis_digest == closed.preexecution_closure_basis.digest

    replay = await handler.execute(
        ReconcilePreexecutionCancellationCommand(
            batch_id=source.id,
            project_id=intent.project_id,
            suite_revision_id=intent.suite_revision_id,
            expected_batch_version=requested.version,
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
    )

    assert replay is closed
    assert state.closure_authority_checks == 2
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1


@pytest.mark.asyncio
async def test_materialized_scope_returns_handoff_input_without_closing_batch() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import (
        MaterializedExecutionScope,
        ProvePreexecutionClosure,
    )

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(batch=requested)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )

    result = await ReconcilePreexecutionCancellation(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    ).execute(
        ReconcilePreexecutionCancellationCommand(
            batch_id=requested.id,
            project_id=intent.project_id,
            suite_revision_id=intent.suite_revision_id,
            expected_batch_version=requested.version,
            reconciler_id="reconciler-001",
            closure_epoch=1,
        )
    )

    assert isinstance(result, MaterializedExecutionScope)
    assert result.run_ids == ("run-001",)
    assert state.batch is requested
    assert state.batch.preexecution_closure_basis is None
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


def _requested_batch():
    from qarunner.domain import (
        Batch,
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchState,
        CancellationSource,
        canonical_digest,
    )

    source = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    intent = BatchCancellationIntent(
        batch_id=source.id,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=source.version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
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
        recorded_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
    )
    return source.request_cancel(intent=intent, expected_version=source.version), intent
