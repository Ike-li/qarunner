"""Application reconciliation for a rejection racing materialized Runs."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_materialized_rejection_publishes_handoff_then_returns_internal_state_conflict() -> (
    None
):
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
        RejectionMaterializedConflict,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )
    handler = RecordPreexecutionRejection(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    )
    command = RecordPreexecutionRejectionCommand(
        rejection=rejection,
        phase_owner_id="coordinator-001",
        rejection_epoch=1,
    )

    with pytest.raises(RejectionMaterializedConflict) as first:
        await handler.execute(command)
    with pytest.raises(RejectionMaterializedConflict) as replay:
        await handler.execute(command)

    assert replay.value.handoff is first.value.handoff
    assert first.value.handoff.trigger_kind.value == "rejection_conflict"
    assert first.value.handoff.command_or_observation_digest == rejection.digest
    assert state.batch is batch
    assert state.batch.rejection_fact is None
    assert state.batch.preexecution_closure_basis is None
    assert len(state.handoffs) == 1
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1
    assert state.rejection_authority_checks == 2
    assert state.run_resolutions == ()
    assert state.fanout_results == ()
    assert state.not_executed_facts == ()
    assert state.run_outcomes == ()


@pytest.mark.asyncio
async def test_rejection_authority_unavailable_fails_before_batch_read_or_handoff_replay() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
        TemporarilyUnavailable,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch, authority_available=False)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )

    with pytest.raises(TemporarilyUnavailable):
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=rejection,
                phase_owner_id="coordinator-001",
                rejection_epoch=1,
            )
        )

    assert state.rejection_authority_checks == 1
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.handoffs == {}


@pytest.mark.asyncio
async def test_rejection_authority_denied_fails_before_batch_read() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        ObjectForbidden,
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch, authority_allowed=False)

    with pytest.raises(ObjectForbidden):
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(
                gateway=InMemoryPreexecutionProofGateway(inventory_sealed=True)
            ),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=rejection,
                phase_owner_id="coordinator-001",
                rejection_epoch=1,
            )
        )

    assert state.batch_reads == 0


@pytest.mark.asyncio
async def test_superseded_rejection_epoch_returns_state_conflict_before_batch_read() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch, current_rejection_epoch=2)

    with pytest.raises(PreexecutionStateConflict) as captured:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(
                gateway=InMemoryPreexecutionProofGateway(inventory_sealed=True)
            ),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=rejection,
                phase_owner_id="coordinator-001",
                rejection_epoch=1,
            )
        )

    assert captured.value.reason == "phase_epoch_superseded"
    assert state.batch_reads == 0


@pytest.mark.asyncio
async def test_retired_phase_owner_returns_state_conflict_before_batch_read_or_replay() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch, rejection_authority_current=False)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(PreexecutionStateConflict) as captured:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=rejection,
                phase_owner_id="retired-phase-owner",
                rejection_epoch=1,
            )
        )

    assert captured.value.http_status == 409
    assert captured.value.reason == "phase_owner_authority_retired"
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_expired_rejection_projection_fails_before_batch_read_or_replay() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
        TemporarilyUnavailable,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    expired_at = datetime(2026, 7, 15, 6, tzinfo=UTC)
    state = InMemoryBatchPreexecutionGateway(
        batch=batch,
        authority_checked_at=expired_at,
        authority_expires_at=expired_at,
    )
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(TemporarilyUnavailable):
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=rejection,
                phase_owner_id="coordinator-001",
                rejection_epoch=1,
            )
        )

    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_rejection_source_binding_drift_fails_before_batch_read_or_replay() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    stale = replace(rejection, source_batch_version=rejection.source_batch_version - 1)
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(PreexecutionStateConflict) as captured:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=stale,
                phase_owner_id="coordinator-001",
                rejection_epoch=1,
            )
        )

    assert captured.value.reason == "source_binding_superseded"
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_zero_child_rejection_atomically_closes_batch_and_exact_replays() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import BatchState
    from qarunner.domain.errors import IdempotencyConflict

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)
    handler = RecordPreexecutionRejection(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    )
    command = RecordPreexecutionRejectionCommand(
        rejection=rejection,
        phase_owner_id="coordinator-001",
        rejection_epoch=1,
    )

    closed = await handler.execute(command)
    replay = await handler.execute(command)

    assert replay is closed
    assert closed.state is BatchState.REJECTED
    assert closed.rejection_fact is rejection
    assert closed.preexecution_closure_basis is not None
    assert state.batch is closed
    assert proof_gateway.child_scans == 1
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1

    with pytest.raises(IdempotencyConflict):
        await handler.execute(
            replace(
                command,
                rejection=replace(rejection, reason_code="changed_reason"),
            )
        )
    assert proof_gateway.child_scans == 1


@pytest.mark.asyncio
async def test_rejection_handoff_publication_failure_rolls_back_before_state_conflict() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(
        batch=batch,
        publication_error=RuntimeError("transaction failed"),
    )

    with pytest.raises(RuntimeError, match="transaction failed"):
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(
                gateway=InMemoryPreexecutionProofGateway(
                    inventory_sealed=False,
                    run_ids=("run-001",),
                )
            ),
        ).execute(
            RecordPreexecutionRejectionCommand(
                rejection=rejection,
                phase_owner_id="coordinator-001",
                rejection_epoch=1,
            )
        )

    assert state.batch is batch
    assert state.handoffs == {}
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_planned_admission_rejection_builds_rejection_bound_item_coverage() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import (
        ProvePreexecutionClosure,
        canonical_materialized_run_set_digest,
    )
    from qarunner.domain import (
        Batch,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        BatchPreexecutionScopeKind,
        BatchPreexecutionTerminalKind,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-planned-rejection.v1",
            payload={"label": label},
        )

    scope = BatchCancellationScope(
        kind=BatchCancellationScopeKind.FROZEN_PLAN,
        preplan_scope_digest=None,
        manifest_digest=digest("manifest"),
        shard_plan_version=2,
        shard_plan_digest=digest("plan"),
        canonical_run_set_digest=canonical_materialized_run_set_digest(
            batch_id="batch-001",
            run_ids=(),
        ),
    )
    batch = Batch(id="batch-001", state=BatchState.AWAITING_ADMISSION, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-admission-001",
        batch_id=batch.id,
        source_batch_version=batch.version,
        stage=BatchRejectionStage.ADMISSION,
        reason_class=BatchRejectionReasonClass.CAPACITY_REJECTED,
        reason_code="capacity_unavailable",
        input_digest=digest("input"),
        authority_digest=digest("admission-authority"),
        recorded_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
    )
    state = InMemoryBatchPreexecutionGateway(batch=batch, authority_scope=scope)
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=True,
        planned_manifest_id="manifest-001",
        planned_manifest_digest=scope.manifest_digest,
        planned_item_keys=("case-001", "case-002"),
        planned_shard_plan_id="plan-001",
        planned_shard_plan_version=scope.shard_plan_version,
        planned_shard_plan_digest=scope.shard_plan_digest,
    )

    closed = await RecordPreexecutionRejection(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    ).execute(
        RecordPreexecutionRejectionCommand(
            rejection=rejection,
            phase_owner_id="admission-001",
            rejection_epoch=1,
        )
    )

    basis = closed.preexecution_closure_basis
    snapshot = proof_gateway.published_snapshots[-1]
    assert basis is not None
    assert basis.scope_kind is BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
    assert tuple(item.manifest_item_key for item in snapshot.scope_items) == (
        "case-001",
        "case-002",
    )
    assert all(
        item.terminal_kind is BatchPreexecutionTerminalKind.REJECTION
        and item.rejection_fact_digest == rejection.digest
        and item.batch_cancellation_intent_digest is None
        for item in snapshot.scope_items
    )
    assert state.run_resolutions == ()
    assert state.not_executed_facts == ()
    assert state.run_outcomes == ()


def _batch_and_rejection():
    from qarunner.domain import (
        Batch,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
        canonical_digest,
    )

    batch = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=batch.id,
        source_batch_version=batch.version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="invalid_suite",
        input_digest=canonical_digest(
            schema_version="qep.test-rejection-input.v1",
            payload={"suite_revision_id": "suite-revision-001"},
        ),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 15, 6, tzinfo=UTC),
    )
    return batch, rejection
