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
async def test_materialized_scope_publishes_one_deterministic_handoff_without_closure() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.handoff import BatchMaterializedScopeHandoff
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(batch=requested)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )

    handler = ReconcilePreexecutionCancellation(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    )
    command = ReconcilePreexecutionCancellationCommand(
        batch_id=requested.id,
        project_id=intent.project_id,
        suite_revision_id=intent.suite_revision_id,
        expected_batch_version=requested.version,
        reconciler_id="reconciler-001",
        closure_epoch=1,
    )

    result = await handler.execute(command)
    replay = await handler.execute(command)

    assert isinstance(result, BatchMaterializedScopeHandoff)
    assert replay is result
    assert result.schema_version == "qep.batch-materialized-scope-handoff.v1"
    assert result.trigger_kind.value == "cancel_intent"
    assert result.destination == "execution_path"
    assert result.authoritative_run_set_digest == proof_gateway.materialized_run_set_digest
    assert result.handoff_id.startswith("handoff-")
    assert result.event_id.startswith("handoff-event-")
    assert state.batch is requested
    assert state.batch.preexecution_closure_basis is None
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1
    assert state.semantic_outbox[0].event_id == result.event_id
    assert state.run_resolutions == ()
    assert state.fanout_results == ()
    assert state.not_executed_facts == ()
    assert state.run_outcomes == ()


@pytest.mark.asyncio
async def test_materialized_scope_without_cancellation_intent_is_rejected() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import Batch, BatchState

    batch = Batch(id="batch-001", state=BatchState.COLLECTING, version=3)
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    handler = ReconcilePreexecutionCancellation(
        gateway=state,
        proof=ProvePreexecutionClosure(
            gateway=InMemoryPreexecutionProofGateway(
                inventory_sealed=False,
                run_ids=("run-001",),
            )
        ),
    )

    with pytest.raises(RuntimeError, match="requires intent"):
        await handler.execute(
            ReconcilePreexecutionCancellationCommand(
                batch_id=batch.id,
                project_id="project-001",
                suite_revision_id="suite-revision-001",
                expected_batch_version=batch.version,
                reconciler_id="reconciler-001",
                closure_epoch=1,
            )
        )

    assert state.handoffs == {}
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_materialized_handoff_binding_drift_is_quarantined_without_second_event() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain.errors import IdempotencyConflict

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(batch=requested)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )
    handler = ReconcilePreexecutionCancellation(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    )
    command = ReconcilePreexecutionCancellationCommand(
        batch_id=requested.id,
        project_id=intent.project_id,
        suite_revision_id=intent.suite_revision_id,
        expected_batch_version=requested.version,
        reconciler_id="reconciler-001",
        closure_epoch=1,
    )
    await handler.execute(command)
    proof_gateway.run_ids = ("run-001", "run-002")

    with pytest.raises(IdempotencyConflict):
        await handler.execute(command)

    assert len(state.handoffs) == 1
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1
    assert len(state.quarantined_handoffs) == 1


@pytest.mark.asyncio
async def test_materialized_handoff_publication_failure_is_atomic() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(
        batch=requested,
        publication_error=RuntimeError("transaction failed"),
    )
    handler = ReconcilePreexecutionCancellation(
        gateway=state,
        proof=ProvePreexecutionClosure(
            gateway=InMemoryPreexecutionProofGateway(
                inventory_sealed=False,
                run_ids=("run-001",),
            )
        ),
    )

    with pytest.raises(RuntimeError, match="transaction failed"):
        await handler.execute(
            ReconcilePreexecutionCancellationCommand(
                batch_id=requested.id,
                project_id=intent.project_id,
                suite_revision_id=intent.suite_revision_id,
                expected_batch_version=requested.version,
                reconciler_id="reconciler-001",
                closure_epoch=1,
            )
        )

    assert state.batch is requested
    assert state.handoffs == {}
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway_options", "expected_problem"),
    [
        ({"authority_available": False}, "TemporarilyUnavailable"),
        ({"authority_allowed": False}, "ObjectForbidden"),
    ],
)
async def test_closure_authority_failure_precedes_batch_read_proof_and_replay(
    gateway_options: dict[str, bool],
    expected_problem: str,
) -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application import batch_preexecution as application
    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(batch=requested, **gateway_options)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )

    with pytest.raises(getattr(application, expected_problem)):
        await ReconcilePreexecutionCancellation(
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

    assert state.closure_authority_checks == 1
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.handoffs == {}


@pytest.mark.asyncio
async def test_superseded_closure_epoch_returns_state_conflict_before_replay() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(batch=requested, current_closure_epoch=2)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )

    with pytest.raises(PreexecutionStateConflict) as captured:
        await ReconcilePreexecutionCancellation(
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

    assert captured.value.reason == "closure_epoch_superseded"
    assert captured.value.http_status == 409
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0


@pytest.mark.asyncio
async def test_expired_authority_projection_fails_closed_without_freezing_a_max_age() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
        TemporarilyUnavailable,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    requested, intent = _requested_batch()
    expired_at = datetime(2026, 7, 15, 6, tzinfo=UTC)
    state = InMemoryBatchPreexecutionGateway(
        batch=requested,
        authority_checked_at=expired_at,
        authority_expires_at=expired_at,
    )

    with pytest.raises(TemporarilyUnavailable):
        await ReconcilePreexecutionCancellation(
            gateway=state,
            proof=ProvePreexecutionClosure(
                gateway=InMemoryPreexecutionProofGateway(inventory_sealed=True)
            ),
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

    assert state.batch_reads == 0


@pytest.mark.asyncio
async def test_closure_source_binding_drift_returns_state_conflict_before_proof() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        ReconcilePreexecutionCancellation,
        ReconcilePreexecutionCancellationCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    requested, intent = _requested_batch()
    state = InMemoryBatchPreexecutionGateway(batch=requested)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(PreexecutionStateConflict) as captured:
        await ReconcilePreexecutionCancellation(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(
            ReconcilePreexecutionCancellationCommand(
                batch_id=requested.id,
                project_id="foreign-project",
                suite_revision_id=intent.suite_revision_id,
                expected_batch_version=requested.version,
                reconciler_id="reconciler-001",
                closure_epoch=1,
            )
        )

    assert captured.value.reason == "source_binding_superseded"
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0


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
