"""T-M0-STATE-001F: fact-aware Batch pre-execution commands."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution.v1",
        payload={"label": label},
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


def _planned_rejection_snapshot(*, rejection):
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
        item_coverage_proof_digest=_digest("item-coverage"),
    )


@pytest.mark.parametrize(
    ("source_name", "target_name"),
    [
        pytest.param("VALIDATING", "REJECTED", id="validation-rejection"),
        pytest.param("AWAITING_ADMISSION", "REJECTED", id="admission-rejection"),
        pytest.param("QUEUED", "CANCELLED", id="queued-cancellation"),
    ],
)
def test_generic_transition_cannot_bypass_fact_aware_terminal_commands(
    source_name: str,
    target_name: str,
) -> None:
    """Rejection and cancellation require immutable facts, not a target enum."""
    from qarunner.domain import Batch, BatchState, InvalidTransition

    source = Batch(id="batch-001", state=BatchState[source_name], version=7)

    with pytest.raises(InvalidTransition):
        source.transition(BatchState[target_name], expected_version=7)

    assert source.state is BatchState[source_name]
    assert source.version == 7


@pytest.mark.parametrize(
    ("state_name", "stage_name", "reason_name", "authority_label"),
    [
        ("VALIDATING", "VALIDATION", "INVALID_INPUT", None),
        ("COLLECTING", "COLLECTION", "SOURCE_FAILURE", None),
        ("PLANNING", "PLANNING", "PLANNING_FAILURE", None),
        ("AWAITING_ADMISSION", "ADMISSION", "CAPACITY_REJECTED", "admission"),
    ],
)
def test_matching_rejection_atomically_closes_batch_with_basis(
    state_name: str,
    stage_name: str,
    reason_name: str,
    authority_label: str | None,
) -> None:
    """A persisted phase failure closes only through its immutable fact."""
    from qarunner.domain import (
        Batch,
        BatchPreexecutionTerminalKind,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
    )

    source = Batch(id="batch-001", state=BatchState[state_name], version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=source.id,
        source_batch_version=source.version,
        stage=BatchRejectionStage[stage_name],
        reason_class=BatchRejectionReasonClass[reason_name],
        reason_code=f"{stage_name.lower()}_failed",
        input_digest=_digest(f"{stage_name.lower()}-input"),
        authority_digest=(
            None if authority_label is None else _digest(f"{authority_label}-authority")
        ),
        recorded_at=datetime(2026, 7, 14, 5, 0, tzinfo=UTC),
    )
    snapshot = (
        _planned_rejection_snapshot(rejection=rejection)
        if source.state is BatchState.AWAITING_ADMISSION
        else _preplan_snapshot(
            batch_id=source.id,
            source_batch_version=source.version,
        )
    )

    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )

    assert rejected.state is BatchState.REJECTED
    assert rejected.version == source.version + 1
    assert rejected.rejection_fact == rejection
    assert rejected.cancellation_intent is None
    assert rejected.preexecution_closure_basis is not None
    assert (
        rejected.preexecution_closure_basis.terminal_kind
        is BatchPreexecutionTerminalKind.REJECTION
    )
    assert rejected.preexecution_closure_basis.rejection_fact_digest == rejection.digest
    assert rejected.preexecution_closure_basis.batch_cancellation_intent_digest is None
    assert rejected.preexecution_closure_basis.scope_kind is snapshot.scope_kind
    assert rejected.preexecution_closure_basis.submission_digest == snapshot.submission_digest
    assert (
        rejected.preexecution_closure_basis.execution_absence_snapshot_digest
        == snapshot.execution_absence_snapshot_digest
    )
    assert source.state is BatchState[state_name]
    assert source.version == 3
    assert source.rejection_fact is None


def test_exact_rejection_replay_returns_stored_terminal_before_stale_cas() -> None:
    """A lost response replay does not create a second fact or version."""
    from qarunner.domain import (
        Batch,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
    )

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=source.id,
        source_batch_version=source.version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="manifest_request_invalid",
        input_digest=_digest("validation-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 14, 5, 0, tzinfo=UTC),
    )
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )

    replay = rejected.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )

    assert replay is rejected
    assert replay.version == 4


def test_same_rejection_id_with_changed_fact_conflicts_before_stale_cas() -> None:
    """A rejection identity cannot be rebound to different immutable content."""
    from qarunner.domain import (
        Batch,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
        IdempotencyConflict,
    )

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=source.id,
        source_batch_version=source.version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="manifest_request_invalid",
        input_digest=_digest("validation-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 14, 5, 0, tzinfo=UTC),
    )
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    changed = replace(rejection, reason_code="different_reason")

    with pytest.raises(IdempotencyConflict) as caught:
        rejected.reject_preexecution(
            rejection=changed,
            snapshot=snapshot,
            expected_version=source.version,
        )

    assert caught.value.scope == "batch:batch-001:rejection"
    assert caught.value.key == rejection.rejection_id
    assert caught.value.stored_digest == rejection.digest
    assert caught.value.received_digest == changed.digest
    assert rejected.version == 4
    assert rejected.rejection_fact == rejection


def test_same_rejection_id_with_changed_snapshot_conflicts_before_stale_cas() -> None:
    """A replay cannot substitute a different no-execution snapshot."""
    from qarunner.domain import (
        Batch,
        BatchRejection,
        BatchRejectionReasonClass,
        BatchRejectionStage,
        BatchState,
        IdempotencyConflict,
    )

    source = Batch(id="batch-001", state=BatchState.VALIDATING, version=3)
    rejection = BatchRejection(
        rejection_id="rejection-001",
        batch_id=source.id,
        source_batch_version=source.version,
        stage=BatchRejectionStage.VALIDATION,
        reason_class=BatchRejectionReasonClass.INVALID_INPUT,
        reason_code="manifest_request_invalid",
        input_digest=_digest("validation-input"),
        authority_digest=None,
        recorded_at=datetime(2026, 7, 14, 5, 0, tzinfo=UTC),
    )
    snapshot = _preplan_snapshot(
        batch_id=source.id,
        source_batch_version=source.version,
    )
    rejected = source.reject_preexecution(
        rejection=rejection,
        snapshot=snapshot,
        expected_version=source.version,
    )
    changed = replace(
        snapshot,
        execution_absence_snapshot_digest=_digest("different-no-execution-snapshot"),
    )

    with pytest.raises(IdempotencyConflict) as caught:
        rejected.reject_preexecution(
            rejection=rejection,
            snapshot=changed,
            expected_version=source.version,
        )

    assert caught.value.scope == "batch:batch-001:rejection"
    assert caught.value.key == rejection.rejection_id
    assert rejected.preexecution_closure_basis is not None
    changed_basis = replace(
        rejected.preexecution_closure_basis,
        execution_absence_snapshot_digest=changed.execution_absence_snapshot_digest,
    )
    assert caught.value.stored_digest == rejected.preexecution_closure_basis.digest
    assert caught.value.received_digest == changed_basis.digest
    assert rejected.version == 4
    assert (
        rejected.preexecution_closure_basis.execution_absence_snapshot_digest
        == snapshot.execution_absence_snapshot_digest
    )
