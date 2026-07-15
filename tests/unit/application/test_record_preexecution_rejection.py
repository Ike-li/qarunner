"""Application reconciliation for a rejection racing materialized Runs."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_rejection_fact_ownership_stage_authority_and_time_are_server_derived() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
        RecordPreexecutionRejectionCommand,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import BatchRejectionStage

    batch, failure = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    command = RecordPreexecutionRejectionCommand(
        batch_id=batch.id,
        rejection_id=failure.rejection_id,
        reason_class=failure.reason_class,
        reason_code=failure.reason_code,
        input_digest=failure.input_digest,
        phase_owner_id="coordinator-001",
        rejection_epoch=1,
    )

    closed = await RecordPreexecutionRejection(
        gateway=state,
        proof=ProvePreexecutionClosure(
            gateway=InMemoryPreexecutionProofGateway(inventory_sealed=True)
        ),
    ).execute(command)

    rejection = closed.rejection_fact
    assert rejection is not None
    assert rejection.batch_id == batch.id
    assert rejection.source_batch_version == batch.version
    assert rejection.stage is BatchRejectionStage.VALIDATION
    assert rejection.authority_digest == state.last_rejection_authority.authority_digest
    assert rejection.recorded_at == state.last_rejection_authority.recorded_at


def test_rejection_command_does_not_accept_a_complete_rejection_fact() -> None:
    from qarunner.application.batch_preexecution import RecordPreexecutionRejectionCommand

    _, rejection = _batch_and_rejection()

    with pytest.raises(TypeError):
        RecordPreexecutionRejectionCommand(
            rejection=rejection,
            phase_owner_id="coordinator-001",
            rejection_epoch=1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_name", "stage_name", "frozen_plan"),
    [
        pytest.param("VALIDATING", "VALIDATION", False, id="validating"),
        pytest.param("COLLECTING", "COLLECTION", False, id="collecting"),
        pytest.param("PLANNING", "PLANNING", False, id="planning"),
        pytest.param("AWAITING_ADMISSION", "ADMISSION", True, id="awaiting-admission"),
    ],
)
async def test_rejection_phase_family_derives_current_stage_source_and_scope(
    state_name: str,
    stage_name: str,
    frozen_plan: bool,
) -> None:
    """Every rejectable phase derives its own current stage, version, and scope."""
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
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
        canonical_digest,
    )

    def digest(label: str):
        return canonical_digest(
            schema_version="qep.test-rejection-phase-family.v1",
            payload={"label": label},
        )

    batch = Batch(id="batch-001", state=BatchState[state_name], version=7)
    if frozen_plan:
        scope = BatchCancellationScope(
            kind=BatchCancellationScopeKind.FROZEN_PLAN,
            preplan_scope_digest=None,
            manifest_digest=digest("manifest"),
            shard_plan_version=2,
            shard_plan_digest=digest("plan"),
            canonical_run_set_digest=canonical_materialized_run_set_digest(
                batch_id=batch.id,
                run_ids=(),
            ),
        )
        proof_gateway = InMemoryPreexecutionProofGateway(
            inventory_sealed=True,
            planned_manifest_id="manifest-001",
            planned_manifest_digest=scope.manifest_digest,
            planned_item_keys=("case-001",),
            planned_shard_plan_id="plan-001",
            planned_shard_plan_version=scope.shard_plan_version,
            planned_shard_plan_digest=scope.shard_plan_digest,
        )
    else:
        scope = BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=digest("preplan"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        )
        proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)
    state = InMemoryBatchPreexecutionGateway(batch=batch, authority_scope=scope)
    command = RecordPreexecutionRejectionCommand(
        batch_id=batch.id,
        rejection_id=f"rejection-{state_name.lower()}",
        reason_class=BatchRejectionReasonClass.POLICY_DENIED,
        reason_code="phase_failure",
        input_digest=digest("input"),
        phase_owner_id="phase-owner-001",
        rejection_epoch=1,
    )

    closed = await RecordPreexecutionRejection(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    ).execute(command)

    assert closed.rejection_fact is not None
    assert closed.rejection_fact.source_batch_version == batch.version
    assert closed.rejection_fact.stage is BatchRejectionStage[stage_name]
    assert state.last_rejection_authority.source_batch_version == batch.version
    assert state.last_rejection_authority.stage is BatchRejectionStage[stage_name]
    assert state.last_rejection_authority.scope is scope
    assert closed.preexecution_closure_basis is not None
    assert closed.preexecution_closure_basis.scope_kind is (
        BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
        if frozen_plan
        else BatchPreexecutionScopeKind.PRE_PLAN
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_name",
    [
        pytest.param("QUEUED", id="queued"),
        pytest.param("RUNNING", id="running"),
        pytest.param("SUCCEEDED", id="succeeded"),
        pytest.param("FAILED", id="failed"),
        pytest.param("PARTIAL", id="partial"),
        pytest.param("CANCELLED", id="cancelled"),
        pytest.param("REJECTED", id="legacy-rejected-without-fact"),
    ],
)
async def test_invalid_rejection_phase_fails_closed_before_batch_read_or_proof(
    state_name: str,
) -> None:
    """An invalid current phase is an authority conflict, never a mapping KeyError."""
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import Batch, BatchState

    _, rejection = _batch_and_rejection()
    batch = Batch(id=rejection.batch_id, state=BatchState[state_name], version=9)
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(PreexecutionStateConflict) as caught:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(_command(rejection))

    assert caught.value.reason == "rejection_phase_not_current"
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_materialized_rejection_publishes_handoff_then_returns_internal_state_conflict() -> (
    None
):
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
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
    command = _command(rejection)

    with pytest.raises(RejectionMaterializedConflict) as first:
        await handler.execute(command)
    with pytest.raises(RejectionMaterializedConflict) as replay:
        await handler.execute(command)

    assert replay.value.handoff is first.value.handoff
    assert first.value.handoff.trigger_kind.value == "rejection_conflict"
    assert (
        first.value.handoff.command_or_observation_digest
        == replay.value.handoff.command_or_observation_digest
    )
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
        ).execute(_command(rejection))

    assert state.rejection_authority_checks == 1
    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.handoffs == {}


@pytest.mark.asyncio
async def test_rejection_authority_denied_fails_before_batch_read() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch, authority_allowed=False)

    with pytest.raises(PreexecutionStateConflict) as caught:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(
                gateway=InMemoryPreexecutionProofGateway(inventory_sealed=True)
            ),
        ).execute(_command(rejection))

    assert state.batch_reads == 0
    assert caught.value.reason == "phase_owner_authority_denied"


@pytest.mark.asyncio
async def test_superseded_rejection_epoch_returns_state_conflict_before_batch_read() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
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
        ).execute(_command(rejection))

    assert captured.value.reason == "phase_epoch_superseded"
    assert state.batch_reads == 0


@pytest.mark.asyncio
async def test_retired_phase_owner_returns_state_conflict_before_batch_read_or_replay() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch, rejection_authority_current=False)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(PreexecutionStateConflict) as captured:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(_command(rejection, phase_owner_id="retired-phase-owner"))

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
        ).execute(_command(rejection))

    assert state.batch_reads == 0
    assert proof_gateway.child_scans == 0
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


def test_rejection_command_rejects_caller_provided_source_binding() -> None:
    from qarunner.application.batch_preexecution import RecordPreexecutionRejectionCommand

    _, rejection = _batch_and_rejection()
    values = (
        _command(rejection).__dict__
        if hasattr(_command(rejection), "__dict__")
        else {
            "batch_id": rejection.batch_id,
            "rejection_id": rejection.rejection_id,
            "reason_class": rejection.reason_class,
            "reason_code": rejection.reason_code,
            "input_digest": rejection.input_digest,
            "phase_owner_id": "coordinator-001",
            "rejection_epoch": 1,
        }
    )

    with pytest.raises(TypeError):
        RecordPreexecutionRejectionCommand(
            **values,
            source_batch_version=rejection.source_batch_version,
        )  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_zero_child_rejection_atomically_closes_batch_and_exact_replays() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
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
    command = _command(rejection)

    closed = await handler.execute(command)
    replay = await handler.execute(command)

    assert replay is closed
    assert closed.state is BatchState.REJECTED
    assert closed.rejection_fact is not rejection
    assert closed.rejection_fact is not None
    assert closed.rejection_fact.rejection_id == rejection.rejection_id
    assert closed.preexecution_closure_basis is not None
    assert state.batch is closed
    assert proof_gateway.child_scans == 1
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1

    with pytest.raises(IdempotencyConflict):
        await handler.execute(
            replace(
                command,
                reason_code="changed_reason",
            )
        )
    assert proof_gateway.child_scans == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_authority_lineage", [False, True], ids=["current", "stale"])
async def test_pending_cancel_winner_blocks_late_rejection_before_proof_or_handoff(
    stale_authority_lineage: bool,
) -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import (
        BatchCancellationIntent,
        CancellationSource,
    )

    batch, rejection = _batch_and_rejection()
    probe = InMemoryBatchPreexecutionGateway(batch=batch)
    authority = probe.last_cancel_authority
    intent = BatchCancellationIntent(
        batch_id=batch.id,
        project_id=authority.project_id,
        suite_revision_id=authority.suite_revision_id,
        source_batch_version=batch.version,
        idempotency_key="cancel-winner-001",
        source=CancellationSource.USER_REQUEST,
        actor_id=authority.actor_id,
        reason="cancel wins",
        authorization_digest=authority.authorization_digest,
        scope=authority.scope,
        recorded_at=authority.recorded_at,
    )
    requested = batch.request_cancel(intent=intent, expected_version=batch.version)
    state = InMemoryBatchPreexecutionGateway(
        batch=requested,
        rejection_source_batch_version=(
            batch.version if stale_authority_lineage else requested.version
        ),
    )
    proof_gateway = InMemoryPreexecutionProofGateway(
        inventory_sealed=False,
        run_ids=("run-001",),
    )

    with pytest.raises(PreexecutionStateConflict) as caught:
        await RecordPreexecutionRejection(
            gateway=state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(_command(rejection))

    assert caught.value.reason == "cancellation_intent_winner"
    assert proof_gateway.child_scans == 0
    assert state.handoffs == {}
    assert state.audit_records == ()
    assert state.semantic_outbox == ()
    assert state.batch is requested


@pytest.mark.asyncio
async def test_different_rejection_id_on_terminal_conflicts_before_proof() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure

    batch, rejection = _batch_and_rejection()
    state = InMemoryBatchPreexecutionGateway(batch=batch)
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)
    handler = RecordPreexecutionRejection(
        gateway=state,
        proof=ProvePreexecutionClosure(gateway=proof_gateway),
    )
    await handler.execute(_command(rejection))

    with pytest.raises(PreexecutionStateConflict) as caught:
        await handler.execute(replace(_command(rejection), rejection_id="rejection-002"))

    assert caught.value.reason == "rejection_terminal_winner"
    assert proof_gateway.child_scans == 1
    assert len(state.audit_records) == 1
    assert len(state.semantic_outbox) == 1


@pytest.mark.asyncio
async def test_rejection_handoff_publication_failure_rolls_back_before_state_conflict() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
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
        ).execute(_command(rejection))

    assert state.batch is batch
    assert state.handoffs == {}
    assert state.audit_records == ()
    assert state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_zero_child_rejection_replay_rejects_current_scope_drift() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        PreexecutionStateConflict,
        RecordPreexecutionRejection,
    )
    from qarunner.application.preexecution_proof import ProvePreexecutionClosure
    from qarunner.domain import (
        BatchCancellationScope,
        BatchCancellationScopeKind,
        canonical_digest,
    )

    batch, rejection = _batch_and_rejection()
    first_state = InMemoryBatchPreexecutionGateway(batch=batch)
    closed = await RecordPreexecutionRejection(
        gateway=first_state,
        proof=ProvePreexecutionClosure(
            gateway=InMemoryPreexecutionProofGateway(inventory_sealed=True)
        ),
    ).execute(_command(rejection))
    drifted_scope = BatchCancellationScope(
        kind=BatchCancellationScopeKind.PRE_PLAN,
        preplan_scope_digest=canonical_digest(
            schema_version="qep.test-rejection-scope.v1",
            payload={"label": "drifted"},
        ),
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        canonical_run_set_digest=None,
    )
    replay_state = InMemoryBatchPreexecutionGateway(
        batch=closed,
        authority_scope=drifted_scope,
    )
    proof_gateway = InMemoryPreexecutionProofGateway(inventory_sealed=True)

    with pytest.raises(PreexecutionStateConflict) as caught:
        await RecordPreexecutionRejection(
            gateway=replay_state,
            proof=ProvePreexecutionClosure(gateway=proof_gateway),
        ).execute(_command(rejection))

    assert caught.value.reason == "rejection_authority_binding_superseded"
    assert proof_gateway.child_scans == 0
    assert replay_state.semantic_outbox == ()


@pytest.mark.asyncio
async def test_planned_admission_rejection_builds_rejection_bound_item_coverage() -> None:
    from tests.fakes.greenfield.batch_preexecution import InMemoryBatchPreexecutionGateway
    from tests.fakes.greenfield.preexecution_proof import InMemoryPreexecutionProofGateway

    from qarunner.application.batch_preexecution import (
        RecordPreexecutionRejection,
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
    ).execute(_command(rejection, phase_owner_id="admission-001"))

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
        and item.rejection_fact_digest == closed.rejection_fact.digest
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


def _command(rejection, *, phase_owner_id: str = "coordinator-001", rejection_epoch: int = 1):
    from qarunner.application.batch_preexecution import RecordPreexecutionRejectionCommand

    return RecordPreexecutionRejectionCommand(
        batch_id=rejection.batch_id,
        rejection_id=rejection.rejection_id,
        reason_class=rejection.reason_class,
        reason_code=rejection.reason_code,
        input_digest=rejection.input_digest,
        phase_owner_id=phase_owner_id,
        rejection_epoch=rejection_epoch,
    )
