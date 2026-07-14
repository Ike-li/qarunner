"""T-M0-STATE-001F: rejection authority, CAS, and cancel-race boundaries."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-rejection-boundary.v1",
        payload={"label": label},
    )


def _rejection(
    *,
    batch_id: str,
    source_batch_version: int,
    stage_name: str = "VALIDATION",
    rejection_id: str = "rejection-001",
):
    from qarunner.domain import (
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
    )

    return BatchRejection(
        rejection_id=rejection_id,
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        stage=BatchRejectionStage[stage_name],
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code=f"{stage_name.lower()}_boundary_failure",
        input_digest=_digest(f"{stage_name.lower()}-input"),
        authority_digest=(_digest("admission-authority") if stage_name == "ADMISSION" else None),
        recorded_at=datetime(2026, 7, 14, 7, 0, tzinfo=UTC),
    )


def _preplan_snapshot(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import BatchPreexecutionScopeKind, BatchPreexecutionSnapshot

    return BatchPreexecutionSnapshot(
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        scope_kind=BatchPreexecutionScopeKind.PRE_PLAN,
        submission_digest=_digest("submission"),
        preplan_scope_digest=_digest("preplan-scope"),
        manifest_digest=None,
        shard_plan_version=None,
        shard_plan_digest=None,
        canonical_run_set_digest=None,
        materialized_run_absence_digest=_digest("no-materialized-runs"),
        execution_absence_snapshot_digest=_digest("no-execution"),
        task_stop_fact_digests=(),
        scope_items=(),
        item_coverage_proof_digest=None,
    )


def _planned_snapshot(*, rejection):
    from qarunner.domain import (
        BatchPreexecutionScopeItem,
        BatchPreexecutionScopeKind,
        BatchPreexecutionSnapshot,
        BatchPreexecutionTerminalKind,
    )

    manifest_digest = _digest("manifest")
    shard_plan_digest = _digest("shard-plan")
    run_absence_digest = _digest("no-materialized-runs")
    return BatchPreexecutionSnapshot(
        batch_id=rejection.batch_id,
        source_batch_version=rejection.source_batch_version,
        scope_kind=BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED,
        submission_digest=_digest("submission"),
        preplan_scope_digest=None,
        manifest_digest=manifest_digest,
        shard_plan_version=1,
        shard_plan_digest=shard_plan_digest,
        canonical_run_set_digest=_digest("empty-run-set"),
        materialized_run_absence_digest=run_absence_digest,
        execution_absence_snapshot_digest=_digest("no-execution"),
        task_stop_fact_digests=(),
        scope_items=(
            BatchPreexecutionScopeItem(
                batch_id=rejection.batch_id,
                source_batch_version=rejection.source_batch_version,
                terminal_kind=BatchPreexecutionTerminalKind.REJECTION,
                rejection_fact_digest=rejection.digest,
                batch_cancellation_intent_digest=None,
                manifest_id="manifest-001",
                manifest_digest=manifest_digest,
                manifest_item_key="case-001",
                shard_plan_id="plan-001",
                shard_plan_version=1,
                shard_plan_digest=shard_plan_digest,
                materialized_run_absence_digest=run_absence_digest,
                resolution="not_started",
            ),
        ),
        item_coverage_proof_digest=_digest("coverage"),
    )


def _snapshot_for(*, state_name: str, rejection):
    if state_name == "AWAITING_ADMISSION":
        return _planned_snapshot(rejection=rejection)
    return _preplan_snapshot(
        batch_id=rejection.batch_id,
        source_batch_version=rejection.source_batch_version,
    )


def _cancel_intent(*, batch_id: str, source_batch_version: int):
    from qarunner.domain import (
        BatchCancellationIntent,
        BatchCancellationScope,
        BatchCancellationScopeKind,
        CancellationSource,
    )

    return BatchCancellationIntent(
        batch_id=batch_id,
        project_id="project-001",
        suite_revision_id="suite-revision-001",
        source_batch_version=source_batch_version,
        idempotency_key="cancel-001",
        source=CancellationSource.USER_REQUEST,
        actor_id="user-001",
        reason="stop before execution starts",
        authorization_digest=_digest("cancel-authorization"),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        recorded_at=datetime(2026, 7, 14, 7, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("state_name", "wrong_stage_name"),
    [
        pytest.param("VALIDATING", "COLLECTION", id="validating"),
        pytest.param("COLLECTING", "PLANNING", id="collecting"),
        pytest.param("PLANNING", "VALIDATION", id="planning"),
        pytest.param("AWAITING_ADMISSION", "PLANNING", id="awaiting-admission"),
    ],
)
def test_each_rejection_phase_rejects_a_fact_from_another_stage(
    state_name: str,
    wrong_stage_name: str,
) -> None:
    """The current phase owner cannot persist another phase's failure fact."""
    from qarunner.domain import Batch, BatchState, DomainValidationError

    source = Batch(id="batch-001", state=BatchState[state_name], version=3)
    rejection = _rejection(
        batch_id=source.id,
        source_batch_version=source.version,
        stage_name=wrong_stage_name,
    )
    snapshot = _snapshot_for(state_name=state_name, rejection=rejection)

    with pytest.raises(DomainValidationError) as caught:
        source.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert caught.value.entity_type == "batch_rejection"
    assert caught.value.field == "stage"
    assert caught.value.reason == "source_phase_mismatch"
    assert source.state is BatchState[state_name]
    assert source.version == 3
    assert source.rejection_fact is None


@pytest.mark.parametrize(
    "drift",
    [
        "rejection_batch_id",
        "snapshot_batch_id",
        "rejection_source_version",
        "snapshot_source_version",
    ],
)
def test_rejection_and_snapshot_must_bind_the_authoritative_batch_version(
    drift: str,
) -> None:
    """Neither command fact nor absence snapshot can drift from the locked Batch."""
    from qarunner.domain import Batch, BatchState, DomainValidationError

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    if drift == "rejection_batch_id":
        rejection = replace(rejection, batch_id="batch-other")
    elif drift == "snapshot_batch_id":
        snapshot = replace(snapshot, batch_id="batch-other")
    elif drift == "rejection_source_version":
        rejection = replace(rejection, source_batch_version=source.version - 1)
    else:
        snapshot = replace(snapshot, source_batch_version=source.version - 1)

    with pytest.raises(DomainValidationError) as caught:
        source.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert caught.value.entity_type == "batch_rejection"
    assert caught.value.field == "source_batch"
    assert caught.value.reason == "mismatch"
    assert source.state is BatchState.VALIDATING
    assert source.version == 3
    assert source.rejection_fact is None


@pytest.mark.parametrize(
    "state_name",
    [
        "DRAFT",
        "QUEUED",
        "RUNNING",
        "FINALIZING",
        "SUCCEEDED",
        "FAILED",
        "PARTIAL",
        "CANCELLED",
        "REJECTED",
    ],
)
def test_rejection_command_is_illegal_outside_the_four_owned_phases(
    state_name: str,
) -> None:
    """Only validation, collection, planning, and admission own rejection."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState[state_name], version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )

    with pytest.raises(InvalidTransition) as caught:
        source.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert caught.value.current_state is BatchState[state_name]
    assert caught.value.requested_state is BatchState.REJECTED
    assert source.state is BatchState[state_name]
    assert source.version == 3
    assert source.rejection_fact is None


def test_new_rejection_mutation_checks_stale_cas_before_payload_boundaries() -> None:
    """A stale new command cannot probe stage or source-binding validation."""
    from qarunner.domain import Batch, BatchState, VersionConflict

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(
        batch_id=source.id,
        source_batch_version=source.version,
        stage_name="COLLECTION",
    )
    snapshot = _preplan_snapshot(
        batch_id="batch-other",
        source_batch_version=source.version,
    )

    with pytest.raises(VersionConflict) as caught:
        source.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=source.version - 1,
        )

    assert caught.value.current_version == 3
    assert caught.value.expected_version == 2
    assert source.state is BatchState.VALIDATING
    assert source.version == 3
    assert source.rejection_fact is None


def test_rejection_snapshot_scope_must_match_the_owned_phase() -> None:
    from qarunner.domain import Batch, BatchState, DomainValidationError

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _planned_snapshot(rejection=rejection)

    with pytest.raises(DomainValidationError) as caught:
        source.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert caught.value.field == "scope_kind"
    assert caught.value.reason == "source_phase_mismatch"


def test_rejection_after_cancel_reread_is_an_invalid_transition() -> None:
    """A durable cancel intent blocks rejection even with the current Batch CAS."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    intent = _cancel_intent(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    cancel_winner = source.request_cancel(intent=intent, expected_version=source.version)
    rejection = _rejection(
        batch_id=cancel_winner.id,
        source_batch_version=cancel_winner.version,
    )
    snapshot = _preplan_snapshot(
        batch_id=cancel_winner.id,
        source_batch_version=cancel_winner.version,
    )

    with pytest.raises(InvalidTransition) as caught:
        cancel_winner.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=cancel_winner.version,
        )

    assert caught.value.current_state is BatchState.VALIDATING
    assert caught.value.requested_state is BatchState.REJECTED
    assert cancel_winner.state is BatchState.VALIDATING
    assert cancel_winner.version == 4
    assert cancel_winner.cancellation_intent is intent
    assert cancel_winner.rejection_fact is None
    assert cancel_winner.preexecution_closure_basis is None
    assert source.version == 3
    assert source.cancellation_intent is None


def test_cancel_winner_precedes_late_rejection_stage_validation() -> None:
    """A reread cancel winner hides rejection payload details from the loser."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    intent = _cancel_intent(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    cancel_winner = source.request_cancel(intent=intent, expected_version=source.version)
    rejection = _rejection(
        batch_id=cancel_winner.id,
        source_batch_version=cancel_winner.version,
        stage_name="COLLECTION",
    )
    snapshot = _preplan_snapshot(
        batch_id=cancel_winner.id,
        source_batch_version=cancel_winner.version,
    )

    with pytest.raises(InvalidTransition) as caught:
        cancel_winner.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=cancel_winner.version,
        )

    assert caught.value.current_state is BatchState.VALIDATING
    assert caught.value.requested_state is BatchState.REJECTED
    assert cancel_winner.state is BatchState.VALIDATING
    assert cancel_winner.version == 4
    assert cancel_winner.cancellation_intent is intent
    assert cancel_winner.rejection_fact is None
    assert cancel_winner.preexecution_closure_basis is None
    assert source.state is BatchState.VALIDATING
    assert source.version == 3
    assert source.cancellation_intent is None


@pytest.mark.parametrize("use_current_version", [False, True], ids=["stale", "current"])
def test_rejection_winner_makes_late_cancel_a_stable_terminal_conflict(
    use_current_version: bool,
) -> None:
    """Rejection wins once; a racing or reread cancel cannot replace terminal truth."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    rejection_winner = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    intent = _cancel_intent(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    expected_version = rejection_winner.version if use_current_version else source.version

    with pytest.raises(InvalidTransition) as caught:
        rejection_winner.request_cancel(
            intent=intent,
            expected_version=expected_version,
        )

    assert caught.value.current_state is BatchState.REJECTED
    assert caught.value.requested_state is BatchState.CANCELLED
    assert rejection_winner.state is BatchState.REJECTED
    assert rejection_winner.version == 4
    assert rejection_winner.rejection_fact is rejection
    assert rejection_winner.cancellation_intent is None
    assert rejection_winner.preexecution_closure_basis is not None
    assert source.state is BatchState.VALIDATING
    assert source.version == 3


def test_cancel_winner_makes_the_original_rejection_race_lose_on_stale_cas() -> None:
    """The first cancel CAS wins and the concurrently built rejection is stale."""
    from qarunner.domain import Batch, BatchState, VersionConflict

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    intent = _cancel_intent(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    cancel_winner = source.request_cancel(intent=intent, expected_version=source.version)

    with pytest.raises(VersionConflict) as caught:
        cancel_winner.reject_preexecution(
            rejection=rejection,
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert caught.value.current_version == 4
    assert caught.value.expected_version == 3
    assert cancel_winner.state is BatchState.VALIDATING
    assert cancel_winner.version == 4
    assert cancel_winner.cancellation_intent is intent
    assert cancel_winner.rejection_fact is None
    assert cancel_winner.preexecution_closure_basis is None
    assert source.version == 3
    assert source.cancellation_intent is None


@pytest.mark.parametrize("use_current_version", [False, True], ids=["stale", "current"])
def test_different_rejection_identity_cannot_reclassify_terminal_winner(
    use_current_version: bool,
) -> None:
    """A terminal Batch returns the same conflict independently of caller CAS age."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = _rejection(batch_id=source.id, source_batch_version=source.version)
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    terminal = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    competing = replace(rejection, rejection_id="rejection-002")
    expected_version = terminal.version if use_current_version else source.version

    with pytest.raises(InvalidTransition) as caught:
        terminal.reject_preexecution(
            rejection=competing,
            snapshot=snapshot,
            expected_version=expected_version,
        )

    assert caught.value.current_state is BatchState.REJECTED
    assert caught.value.requested_state is BatchState.REJECTED
    assert terminal.state is BatchState.REJECTED
    assert terminal.version == 4
    assert terminal.rejection_fact is rejection
    assert terminal.preexecution_closure_basis is not None
    assert source.state is BatchState.VALIDATING
    assert source.version == 3
