"""T-M0-STATE-001F: planned zero-Run cancellation and supplied task-stop refs.

The current domain has no authoritative task inventory. These tests therefore
bind and preserve supplied stop-fact digests without claiming completeness.
"""

from datetime import UTC, datetime

import pytest


def _digest(label: str):
    from qarunner.domain import canonical_digest

    return canonical_digest(
        schema_version="qep.test-batch-preexecution-cancel-planned.v1",
        payload={"label": label},
    )


def _ordered_digests(*labels: str):
    return tuple(sorted((_digest(label) for label in labels), key=lambda item: item.value))


def _frozen_plan_intent(*, batch_id: str, source_batch_version: int):
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
        reason="stop the frozen plan before materialization",
        authorization_digest=_digest("cancel-authorization"),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.FROZEN_PLAN,
            preplan_scope_digest=None,
            manifest_digest=_digest("manifest"),
            shard_plan_version=2,
            shard_plan_digest=_digest("shard-plan-v2"),
            canonical_run_set_digest=_digest("canonical-empty-run-set"),
        ),
        recorded_at=datetime(2026, 7, 14, 7, tzinfo=UTC),
    )


def _preplan_intent(*, batch_id: str, source_batch_version: int):
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
        reason="stop pre-plan work before execution",
        authorization_digest=_digest("cancel-authorization"),
        scope=BatchCancellationScope(
            kind=BatchCancellationScopeKind.PRE_PLAN,
            preplan_scope_digest=_digest("preplan-scope"),
            manifest_digest=None,
            shard_plan_version=None,
            shard_plan_digest=None,
            canonical_run_set_digest=None,
        ),
        recorded_at=datetime(2026, 7, 14, 7, tzinfo=UTC),
    )


def _cancel_requested(*, state_name: str, frozen_plan: bool):
    from qarunner.domain import Batch, BatchState

    source = Batch(id="batch-001", state=BatchState[state_name], version=3)
    intent = (
        _frozen_plan_intent(batch_id=source.id, source_batch_version=source.version)
        if frozen_plan
        else _preplan_intent(batch_id=source.id, source_batch_version=source.version)
    )
    requested = source.request_cancel(intent=intent, expected_version=source.version)
    return source, intent, requested


def _planned_snapshot(
    *,
    batch_id: str,
    source_batch_version: int,
    intent_digest,
    manifest_digest=None,
    shard_plan_version: int = 2,
    shard_plan_digest=None,
    canonical_run_set_digest=None,
):
    from qarunner.domain import (
        BatchPreexecutionScopeItem,
        BatchPreexecutionScopeKind,
        BatchPreexecutionSnapshot,
        BatchPreexecutionTerminalKind,
    )

    manifest_digest = manifest_digest or _digest("manifest")
    shard_plan_digest = shard_plan_digest or _digest("shard-plan-v2")
    canonical_run_set_digest = canonical_run_set_digest or _digest("canonical-empty-run-set")
    run_absence_digest = _digest("no-materialized-runs")
    items = tuple(
        BatchPreexecutionScopeItem(
            batch_id=batch_id,
            source_batch_version=source_batch_version,
            terminal_kind=BatchPreexecutionTerminalKind.PRESTART_CANCEL,
            rejection_fact_digest=None,
            batch_cancellation_intent_digest=intent_digest,
            manifest_id="manifest-001",
            manifest_digest=manifest_digest,
            manifest_item_key=item_key,
            shard_plan_id="plan-001",
            shard_plan_version=shard_plan_version,
            shard_plan_digest=shard_plan_digest,
            materialized_run_absence_digest=run_absence_digest,
            resolution="not_started",
        )
        for item_key in ("case-001", "case-002")
    )
    return BatchPreexecutionSnapshot(
        batch_id=batch_id,
        source_batch_version=source_batch_version,
        scope_kind=BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED,
        submission_digest=_digest("submission"),
        preplan_scope_digest=None,
        manifest_digest=manifest_digest,
        shard_plan_version=shard_plan_version,
        shard_plan_digest=shard_plan_digest,
        canonical_run_set_digest=canonical_run_set_digest,
        materialized_run_absence_digest=run_absence_digest,
        execution_absence_snapshot_digest=_digest("no-execution"),
        task_stop_fact_digests=(),
        scope_items=items,
        item_coverage_proof_digest=_digest("item-coverage"),
    )


def _preplan_snapshot(
    *,
    batch_id: str,
    source_batch_version: int,
    task_stop_fact_digests,
):
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
        task_stop_fact_digests=task_stop_fact_digests,
        scope_items=(),
        item_coverage_proof_digest=None,
    )


@pytest.mark.parametrize(
    "state_name",
    [
        pytest.param("AWAITING_ADMISSION", id="awaiting-admission"),
        pytest.param("QUEUED", id="queued"),
    ],
)
def test_frozen_plan_cancel_closes_from_typed_unmaterialized_item_facts(
    state_name: str,
) -> None:
    """Every planned item is bound to the intent and retained in the basis."""
    from qarunner.domain import (
        BatchPreexecutionScopeKind,
        BatchPreexecutionTerminalKind,
        BatchState,
    )

    source, intent, requested = _cancel_requested(
        state_name=state_name,
        frozen_plan=True,
    )
    snapshot = _planned_snapshot(
        batch_id=requested.id,
        source_batch_version=requested.version,
        intent_digest=intent.digest,
    )

    cancelled = requested.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )

    assert requested.version == source.version + 1
    assert requested.state is BatchState[state_name]
    assert cancelled.state is BatchState.CANCELLED
    assert cancelled.version == requested.version + 1
    assert cancelled.cancellation_intent is intent
    assert cancelled.preexecution_closure_basis is not None
    basis = cancelled.preexecution_closure_basis
    assert basis.source_batch_version == requested.version
    assert basis.source_phase is BatchState[state_name]
    assert basis.terminal_kind is BatchPreexecutionTerminalKind.PRESTART_CANCEL
    assert basis.rejection_fact_digest is None
    assert basis.batch_cancellation_intent_digest == intent.digest
    assert basis.scope_kind is BatchPreexecutionScopeKind.PLANNED_UNMATERIALIZED
    assert basis.submission_digest == snapshot.submission_digest
    assert basis.preplan_scope_digest is None
    assert basis.manifest_digest == snapshot.manifest_digest
    assert basis.shard_plan_version == snapshot.shard_plan_version
    assert basis.shard_plan_digest == snapshot.shard_plan_digest
    assert basis.canonical_run_set_digest == snapshot.canonical_run_set_digest
    assert basis.materialized_run_absence_digest == snapshot.materialized_run_absence_digest
    assert basis.execution_absence_snapshot_digest == (snapshot.execution_absence_snapshot_digest)
    assert basis.task_stop_fact_digests == ()
    assert basis.preexecution_scope_item_fact_digests == tuple(
        item.digest for item in snapshot.scope_items
    )
    assert basis.item_coverage_proof_digest == snapshot.item_coverage_proof_digest
    assert all(
        item.batch_cancellation_intent_digest == intent.digest for item in snapshot.scope_items
    )
    assert source.cancellation_intent is None
    assert requested.preexecution_closure_basis is None


@pytest.mark.parametrize(
    "drift",
    [
        pytest.param("manifest", id="manifest"),
        pytest.param("plan-version", id="plan-version"),
        pytest.param("plan-digest", id="plan-digest"),
        pytest.param("run-set", id="canonical-run-set"),
    ],
)
def test_planned_cancel_rejects_snapshot_scope_drift_from_intent(drift: str) -> None:
    """A self-consistent snapshot cannot replace the scope frozen by intent."""
    from qarunner.domain import DomainValidationError

    _, intent, requested = _cancel_requested(
        state_name="AWAITING_ADMISSION",
        frozen_plan=True,
    )
    changes = {
        "manifest": {"manifest_digest": _digest("different-manifest")},
        "plan-version": {"shard_plan_version": 3},
        "plan-digest": {"shard_plan_digest": _digest("different-plan")},
        "run-set": {"canonical_run_set_digest": _digest("different-empty-run-set")},
    }[drift]
    snapshot = _planned_snapshot(
        batch_id=requested.id,
        source_batch_version=requested.version,
        intent_digest=intent.digest,
        **changes,
    )

    with pytest.raises(DomainValidationError) as caught:
        requested.finalize_unmaterialized_cancel(
            snapshot=snapshot,
            expected_version=requested.version,
        )

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == "scope"
    assert caught.value.reason == "cancellation_intent_mismatch"
    assert requested.version == 4
    assert requested.preexecution_closure_basis is None


def test_planned_cancel_rejects_scope_items_bound_to_another_command() -> None:
    """Typed items must bind the exact PRESTART_CANCEL intent digest."""
    from qarunner.domain import DomainValidationError

    _, intent, requested = _cancel_requested(
        state_name="QUEUED",
        frozen_plan=True,
    )
    snapshot = _planned_snapshot(
        batch_id=requested.id,
        source_batch_version=requested.version,
        intent_digest=_digest("different-cancel-intent"),
    )

    with pytest.raises(DomainValidationError) as caught:
        requested.finalize_unmaterialized_cancel(
            snapshot=snapshot,
            expected_version=requested.version,
        )

    assert caught.value.entity_type == "batch_preexecution_snapshot"
    assert caught.value.field == "scope_items"
    assert caught.value.reason == "command_binding_mismatch"
    assert all(
        item.batch_cancellation_intent_digest != intent.digest for item in snapshot.scope_items
    )
    assert requested.version == 4
    assert requested.preexecution_closure_basis is None


@pytest.mark.parametrize(
    "state_name",
    [
        pytest.param("COLLECTING", id="collecting"),
        pytest.param("PLANNING", id="planning"),
    ],
)
def test_supplied_task_stop_fact_digests_are_preserved_in_cancel_basis(
    state_name: str,
) -> None:
    """Provided stop refs are signed without claiming inventory completeness."""
    _, intent, requested = _cancel_requested(
        state_name=state_name,
        frozen_plan=False,
    )
    task_stop_fact_digests = _ordered_digests("collector-stop", "planner-stop")
    snapshot = _preplan_snapshot(
        batch_id=requested.id,
        source_batch_version=requested.version,
        task_stop_fact_digests=task_stop_fact_digests,
    )

    cancelled = requested.finalize_unmaterialized_cancel(
        snapshot=snapshot,
        expected_version=requested.version,
    )

    assert cancelled.cancellation_intent is intent
    assert cancelled.preexecution_closure_basis is not None
    assert cancelled.preexecution_closure_basis.task_stop_fact_digests == task_stop_fact_digests
